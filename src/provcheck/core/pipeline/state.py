"""Stage transitions. ``advance`` is idempotent: it only moves a case forward from the
stage the caller says it just finished, under a row lock."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from provcheck.core.db import audit
from provcheck.core.db.models import Case
from provcheck.core.interfaces import Queue

log = logging.getLogger(__name__)

# Build step 1: capture is the only stage. Later steps insert extracting/investigating/scoring.
NEXT_STAGE: dict[str, str] = {
    "queued": "capturing",
    "capturing": "complete",
}
# stage -> (task name, queue, timeout seconds)
STAGE_TASKS: dict[str, tuple[str, str, int]] = {
    "capturing": ("pipeline.capture", "capture", 180),
}
TERMINAL = frozenset({"complete", "failed"})


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
