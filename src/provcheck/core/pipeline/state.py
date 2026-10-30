"""Stage transitions.

`advance` is idempotent: it only moves a case forward from the stage the caller
says it just finished, under a row lock. The investigator fan-out uses
`advance_investigation` instead, which only advances once every expected
investigator_runs row is in a terminal state.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from provcheck.core.db import audit
from provcheck.core.db.models import Case, InvestigatorRun
from provcheck.core.interfaces import Queue

log = logging.getLogger(__name__)

# Pipeline for v0.1: capture (URL only) → extract → investigate → score → complete.
# Text submissions skip capture; upload submissions skip capture + extract
# (API starts them at `investigating`).
NEXT_STAGE: dict[str, str] = {
    "queued": "capturing",
    "capturing": "extracting",
    "extracting": "investigating",
    "investigating": "scoring",
    "scoring": "complete",
}
# stage -> (task name, queue, timeout seconds)
STAGE_TASKS: dict[str, tuple[str, str, int]] = {
    "capturing": ("pipeline.capture", "capture", 180),
    "extracting": ("pipeline.extract", "extract", 120),
    "investigating": ("pipeline.fan_out", "orchestrator", 60),
    "scoring": ("pipeline.score", "scoring", 60),
}
TERMINAL = frozenset({"complete", "failed"})
TERMINAL_RUN = frozenset({"done", "failed", "skipped"})

# Investigators active in step 4.
INVESTIGATORS: tuple[str, ...] = (
    "claimcheck",
    "provenance",
    "source",
    "corroboration",
    "media",
)
INVESTIGATOR_QUEUES: dict[str, str] = {
    "claimcheck": "claimcheck",
    "provenance": "provenance",
    "source": "source",
    "corroboration": "corroboration",
    "media": "media",
}


def enqueue_stage(queue: Queue, case_id: uuid.UUID, stage: str) -> None:
    task = STAGE_TASKS.get(stage)
    if task is None:
        return
    name, qname, timeout = task
    queue.enqueue(
        name,
        {"case_id": str(case_id)},
        queue=qname,
        job_id=f"{case_id}-{stage}",
        timeout_s=timeout,
    )


def advance(session: Session, case_id: uuid.UUID, finished: str) -> str | None:
    """Move ``case_id`` from ``finished`` to its next stage. Returns the new stage, or None
    if the case was not in ``finished`` (duplicate or stale call). Caller commits, then
    calls ``enqueue_stage`` for the returned stage."""
    case = session.get(Case, case_id, with_for_update=True)
    if case is None or case.status != finished:
        log.info(
            "advance ignored: case=%s status=%s finished=%s",
            case_id,
            case and case.status,
            finished,
        )
        return None
    nxt = NEXT_STAGE[finished]
    case.status = nxt
    audit.append(session, "stage.transition", {"from": finished, "to": nxt}, case_id=case_id)
    return nxt


def advance_investigation(session: Session, case_id: uuid.UUID) -> str | None:
    """Called after each investigator run finishes. If every expected run is
    terminal, moves the case from 'investigating' to 'scoring'.

    The expected-investigator set is read from ``cases.expected_investigators``
    — a per-case snapshot taken at fan-out time — rather than ``INVESTIGATORS``,
    so a release that grows the investigator list can't strand mid-flight
    cases waiting for a run that was never created. Pre-migration cases
    (NULL column) fall back to today's ``INVESTIGATORS`` for compatibility.
    """
    case = session.get(Case, case_id, with_for_update=True)
    if case is None or case.status != "investigating":
        return None
    runs = session.query(InvestigatorRun).filter(InvestigatorRun.case_id == case_id).all()
    snapshot = case.expected_investigators
    expected = set(snapshot) if snapshot else set(INVESTIGATORS)
    present = {r.investigator: r.status for r in runs}
    if not expected.issubset(present.keys()):
        return None
    if any(present[inv] not in TERMINAL_RUN for inv in expected):
        return None
    case.status = "scoring"
    audit.append(
        session,
        "stage.transition",
        {
            "from": "investigating",
            "to": "scoring",
            "runs": {inv: present[inv] for inv in expected},
            "expected_investigators": sorted(expected),
        },
        case_id=case_id,
    )
    return "scoring"


def fail_case(session: Session, case_id: uuid.UUID, stage: str, reason: str, detail: str) -> None:
    case = session.get(Case, case_id, with_for_update=True)
    if case is None or case.status in TERMINAL:
        return
    case.status = "failed"
    case.error = f"{reason}: {detail}"[:2000]
    audit.append(
        session,
        "case.failed",
        {"stage": stage, "reason": reason, "detail": detail[:2000]},
        case_id=case_id,
    )
