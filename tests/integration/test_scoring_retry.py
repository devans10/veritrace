"""Scoring stage is retry-safe.

A worker killed between the three transactions of `run_score` must not
produce duplicate Score / Report rows on retry, and must still advance the
case to `complete`. Review item #1 from the v0.1 PR review.
"""

from __future__ import annotations

import os
import time
import uuid

import httpx
import psycopg
import pytest

pytestmark = pytest.mark.integration

API = os.environ.get("TEST_API_URL", "http://127.0.0.1:8000")
FIXTURE = "http://fixture-site"


def _db() -> psycopg.Connection:
    return psycopg.connect(
        host="127.0.0.1",
        port=55432,
        dbname="provcheck",
        user="provcheck_owner",
        password=os.environ["DB_OWNER_PASSWORD"],
        autocommit=True,
    )


def _submit_and_wait_for_scoring(url: str, timeout_s: float = 60) -> str:
    """Submit a URL and poll until the case is at least past investigating."""
    r = httpx.post(
        f"{API}/v1/cases",
        json={"url": url, "archive": False, "refresh": True},
        timeout=30,
    )
    assert r.status_code in (200, 202), r.text
    case_id = r.json()["id"]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        d = httpx.get(f"{API}/v1/cases/{case_id}", timeout=10).json()
        if d["status"] == "complete":
            return case_id
        if d["status"] == "failed":
            raise AssertionError(f"case failed: {d['error']}")
        time.sleep(0.3)
    raise AssertionError(f"case {case_id} did not finish: {d['status']}")


def test_at_most_one_score_per_case():
    """The UNIQUE (case_id) constraint from migration 0004 guarantees this
    even if the retry path has a bug."""
    url = f"{FIXTURE}/article.html?nocache={uuid.uuid4().hex}"
    case_id = _submit_and_wait_for_scoring(url)
    with _db() as conn:
        row = conn.execute("SELECT COUNT(*) FROM scores WHERE case_id = %s", (case_id,)).fetchone()
    assert row is not None
    assert row[0] == 1


def test_at_most_one_report_per_case():
    url = f"{FIXTURE}/article.html?nocache={uuid.uuid4().hex}"
    case_id = _submit_and_wait_for_scoring(url)
    with _db() as conn:
        row = conn.execute("SELECT COUNT(*) FROM reports WHERE case_id = %s", (case_id,)).fetchone()
    assert row is not None
    assert row[0] == 1


def test_resume_after_crashed_scoring_does_not_duplicate_score():
    """Simulate a worker crash between score and report by:
    1. Letting the pipeline run to complete normally.
    2. Rewinding the case to `scoring` and re-queueing the task.
    3. Asserting the retry doesn't insert a second score row.
    """
    url = f"{FIXTURE}/article.html?nocache={uuid.uuid4().hex}"
    case_id = _submit_and_wait_for_scoring(url)

    with _db() as conn:
        # Rewind only `cases.status` — Score, Report, artifacts stay.
        conn.execute("UPDATE cases SET status = 'scoring' WHERE id = %s", (case_id,))
        pre_score = conn.execute("SELECT id FROM scores WHERE case_id = %s", (case_id,)).fetchone()
        pre_report = conn.execute(
            "SELECT id FROM reports WHERE case_id = %s", (case_id,)
        ).fetchone()
    assert pre_score is not None and pre_report is not None

    # Re-queue the scoring task via the API's queue. The worker-scoring
    # service picks it up; the retry path must detect the existing Score and
    # existing Report and simply advance to complete without inserting dups.
    import redis as _redis_pkg

    r = _redis_pkg.from_url(f"redis://:{os.environ['REDIS_PASSWORD']}@127.0.0.1:56379/0")
    import rq

    queue = rq.Queue("scoring", connection=r)
    queue.enqueue(
        "provcheck.services.worker.tasks.dispatch",
        "pipeline.score",
        {"case_id": case_id},
        job_id=f"{case_id}-scoring-retry",
    )

    deadline = time.time() + 30
    while time.time() < deadline:
        d = httpx.get(f"{API}/v1/cases/{case_id}", timeout=10).json()
        if d["status"] == "complete":
            break
        time.sleep(0.3)
    else:
        raise AssertionError(f"retry did not complete: {d['status']}")

    with _db() as conn:
        n_scores = conn.execute(
            "SELECT COUNT(*) FROM scores WHERE case_id = %s", (case_id,)
        ).fetchone()
        n_reports = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE case_id = %s", (case_id,)
        ).fetchone()
        same_score = conn.execute("SELECT id FROM scores WHERE case_id = %s", (case_id,)).fetchone()
    assert n_scores[0] == 1  # type: ignore[index]
    assert n_reports[0] == 1  # type: ignore[index]
    # And it's the SAME Score row the first run produced.
    assert same_score[0] == pre_score[0]  # type: ignore[index]


def test_resume_audit_entry_appears_on_retry():
    """After a retry, the audit chain carries evidence that we took the
    retry path (either `score.computed resumed=true` or
    `report.resumed`)."""
    url = f"{FIXTURE}/article.html?nocache={uuid.uuid4().hex}"
    case_id = _submit_and_wait_for_scoring(url)

    with _db() as conn:
        conn.execute("UPDATE cases SET status = 'scoring' WHERE id = %s", (case_id,))

    import redis as _redis_pkg

    r = _redis_pkg.from_url(f"redis://:{os.environ['REDIS_PASSWORD']}@127.0.0.1:56379/0")
    import rq

    rq.Queue("scoring", connection=r).enqueue(
        "provcheck.services.worker.tasks.dispatch",
        "pipeline.score",
        {"case_id": case_id},
        job_id=f"{case_id}-scoring-audit-retry",
    )

    deadline = time.time() + 30
    while time.time() < deadline:
        d = httpx.get(f"{API}/v1/cases/{case_id}", timeout=10).json()
        if d["status"] == "complete":
            break
        time.sleep(0.3)

    audit_entries = httpx.get(f"{API}/v1/cases/{case_id}/audit").json()["entries"]
    assert any(e["event_type"] == "report.resumed" for e in audit_entries), [
        e["event_type"] for e in audit_entries
    ]
