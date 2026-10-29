"""Database-level invariants (CHECK + UNIQUE) from migration 0004.

Pydantic enforces strength / confidence ∈ [0, 1] on extraction, but a buggy
future investigator could write a raw row with out-of-range values. The
CHECK constraints make that an IntegrityError at the DB layer.

The UNIQUE (case_id) on scores is the retry safety net: if a crashed
scoring stage retries, we must not end up with two Score rows.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

pytestmark = pytest.mark.integration


def _db() -> psycopg.Connection:
    return psycopg.connect(
        host="127.0.0.1",
        port=55432,
        dbname="provcheck",
        user="provcheck_owner",
        password=os.environ["DB_OWNER_PASSWORD"],
        autocommit=True,
    )


def _mk_case(conn: psycopg.Connection) -> uuid.UUID:
    cid = uuid.uuid4()
    conn.execute(
        """
        INSERT INTO cases
          (id, created_at, updated_at, input_type, status, depth,
           archive_opt_out)
        VALUES (%s, now(), now(), 'url', 'queued', 'full', false)
        """,
        (cid,),
    )
    return cid


def test_evidence_strength_rejects_out_of_range():
    cid = _mk_case(conn := _db())
    with conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            """
            INSERT INTO evidence_items
              (id, case_id, investigator, signal, finding, direction,
               strength, confidence)
            VALUES (%s, %s, 't', 's', 'f', 'undermines', 1.5, 0.5)
            """,
            (uuid.uuid4(), cid),
        )
    conn.close()


def test_evidence_confidence_rejects_out_of_range():
    cid = _mk_case(conn := _db())
    with conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            """
            INSERT INTO evidence_items
              (id, case_id, investigator, signal, finding, direction,
               strength, confidence)
            VALUES (%s, %s, 't', 's', 'f', 'undermines', 0.5, -0.1)
            """,
            (uuid.uuid4(), cid),
        )
    conn.close()


def test_scores_unique_case_id():
    cid = _mk_case(conn := _db())
    conn.execute(
        """
        INSERT INTO scores
          (id, case_id, scorer_version, config_sha256, band,
           confidence_level, raw_logit)
        VALUES (%s, %s, 'test-v1', 'deadbeef', 'uncertain', 'low', 0.0)
        """,
        (uuid.uuid4(), cid),
    )
    with conn, pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(
            """
            INSERT INTO scores
              (id, case_id, scorer_version, config_sha256, band,
               confidence_level, raw_logit)
            VALUES (%s, %s, 'test-v2', 'cafef00d', 'low_risk', 'medium', 1.0)
            """,
            (uuid.uuid4(), cid),
        )
    conn.close()


def test_cases_expected_investigators_defaults_null():
    cid = _mk_case(conn := _db())
    row = conn.execute(
        "SELECT expected_investigators FROM cases WHERE id = %s", (cid,)
    ).fetchone()
    assert row is not None
    assert row[0] is None
    conn.close()
