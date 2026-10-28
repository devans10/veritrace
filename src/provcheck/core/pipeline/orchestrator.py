"""Investigator fan-out task.

Runs when the case enters 'investigating'. Creates one InvestigatorRun row per
active investigator and enqueues each on its own queue. Idempotent — if a row
already exists for an investigator, it is not duplicated (unique index enforces
this at the DB layer too).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from provcheck.core.db import audit
from provcheck.core.db.models import Case, InvestigatorRun
from provcheck.core.db.session import session_scope
from provcheck.core.pipeline import state
from provcheck.core.providers.registry import Providers, get_providers

log = logging.getLogger(__name__)


def run_fan_out(case_id: uuid.UUID, providers: Providers) -> None:
    with session_scope() as s:
        case = s.get(Case, case_id, with_for_update=True)
        if case is None or case.status != "investigating":
            log.info("fan_out skipped: case=%s status=%s", case_id, case and case.status)
            return
        existing = {
            r.investigator
            for r in s.query(InvestigatorRun).filter(InvestigatorRun.case_id == case_id).all()
        }
        created: list[tuple[str, uuid.UUID]] = []
        for investigator in state.INVESTIGATORS:
            if investigator in existing:
                continue
            run = InvestigatorRun(
                id=uuid.uuid4(),
                case_id=case_id,
                investigator=investigator,
                status="queued",
            )
            s.add(run)
            s.flush()
            created.append((investigator, run.id))
        if created:
            audit.append(
                s,
                "investigators.fan_out",
                {"created": [{"investigator": inv, "run_id": str(rid)} for inv, rid in created]},
                case_id=case_id,
            )

    for investigator, run_id in created:
        qname = state.INVESTIGATOR_QUEUES.get(investigator, "orchestrator")
        providers.queue.enqueue(
            f"investigator.{investigator}",
            {"case_id": str(case_id), "run_id": str(run_id)},
            queue=qname,
            job_id=f"{case_id}-{investigator}",
            timeout_s=300,
        )


def start_run(case_id: uuid.UUID, run_id: uuid.UUID) -> InvestigatorRun | None:
    """Mark a run as 'running'. Returns the row, or None if the run has already started."""
    with session_scope() as s:
        run = s.get(InvestigatorRun, run_id, with_for_update=True)
        if run is None or run.case_id != case_id or run.status != "queued":
            return None
        run.status = "running"
        run.started_at = datetime.now(UTC)
        audit.append(
            s,
            "investigator.started",
            {"investigator": run.investigator, "run_id": str(run_id)},
            case_id=case_id,
        )
        s.flush()
        s.expunge(run)
        return run


def finish_run(
    case_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    status: str,
    error: str | None = None,
    providers_used: dict[str, str] | None = None,
    counts: dict[str, int] | None = None,
) -> None:
    """Mark a run terminal (done|failed|skipped). If every investigator run is
    terminal, advance the case to ``scoring`` and enqueue the scoring task.
    """
    advanced_to: str | None = None
    with session_scope() as s:
        run = s.get(InvestigatorRun, run_id, with_for_update=True)
        if run is None or run.status in state.TERMINAL_RUN:
            return
        run.status = status
        run.finished_at = datetime.now(UTC)
        run.error = (error or None) and str(error)[:2000]
        run.providers = providers_used or {}
        audit.append(
            s,
            "investigator.finished",
            {
                "investigator": run.investigator,
                "run_id": str(run_id),
                "status": status,
                "error": error,
                "counts": counts or {},
            },
            case_id=case_id,
        )
        advanced_to = state.advance_investigation(s, case_id)
    if advanced_to:
        state.enqueue_stage(get_providers().queue, case_id, advanced_to)
