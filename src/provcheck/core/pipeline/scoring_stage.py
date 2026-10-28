"""Scoring stage.

Runs after ``advance_investigation`` moves the case to ``scoring``. Reads all
evidence rows, computes a :class:`ScoreResult`, writes a ``scores`` row, builds
the report JSON, stores it as a ``report_json`` artifact, writes a ``reports``
row, and advances the case to ``complete``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid

from provcheck.core.db import audit
from provcheck.core.db.models import Artifact, Case, EvidenceItem, Report, Score
from provcheck.core.db.session import session_scope
from provcheck.core.pipeline import state
from provcheck.core.providers.registry import Providers
from provcheck.core.report.builder import build_report
from provcheck.core.scoring import default_scoring_config, score_case

log = logging.getLogger(__name__)
STAGE = "scoring"


def _canonical_json(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def run_score(case_id: uuid.UUID, providers: Providers) -> None:
    config = default_scoring_config()

    with session_scope() as s:
        case = s.get(Case, case_id, with_for_update=True)
        if case is None or case.status != STAGE:
            log.info("score skipped: case=%s status=%s", case_id, case and case.status)
            return
        audit.append(s, "stage.started", {"stage": STAGE}, case_id=case_id)
        evidence = list(s.query(EvidenceItem).filter(EvidenceItem.case_id == case_id).all())
        # Expunge so we can use these outside the transaction.
        for e in evidence:
            s.expunge(e)

    try:
        result = score_case(evidence, config)
    except Exception as e:
        log.exception("score crashed: case=%s", case_id)
        with session_scope() as s:
            state.fail_case(s, case_id, STAGE, "error", f"{type(e).__name__}: {e}")
        return

    # Persist Score row.
    with session_scope() as s:
        score = Score(
            id=uuid.uuid4(),
            case_id=case_id,
            scorer_version=result.scorer_version,
            config_sha256=result.config_sha256,
            band=result.band,
            confidence_level=result.confidence_level,
            raw_logit=float(result.raw_logit),
            coverage=result.coverage,
            category_breakdown=[
                {
                    "category": cat.category,
                    "total_abs": cat.total_abs,
                    "evidence_ids": [str(eid) for eid in cat.evidence_ids],
                }
                for cat in result.category_breakdown
            ],
            contributions=[
                {
                    "evidence_id": str(c.evidence_id),
                    "signal": c.signal,
                    "family": c.family,
                    "category": c.category,
                    "direction": c.direction,
                    "value": c.value,
                    "raw": c.raw,
                    "weight": c.weight,
                    "cap": c.cap,
                }
                for c in result.contributions
            ],
        )
        s.add(score)
        audit.append(
            s,
            "score.computed",
            {
                "scorer_version": result.scorer_version,
                "config_sha256": result.config_sha256,
                "band": result.band,
                "confidence_level": result.confidence_level,
                "contributions": len(result.contributions),
                "unknown_signals": result.unknown_signals,
            },
            case_id=case_id,
        )
        s.flush()
        score_id = score.id

    # Build and store the report JSON.
    with session_scope() as s:
        case_row = s.get(Case, case_id)
        score_row = s.get(Score, score_id)
        assert case_row is not None and score_row is not None
        report_doc = build_report(
            session=s, case=case_row, score=score_row, score_result=result
        )
    data = _canonical_json(report_doc)
    obj = asyncio.run(
        providers.object_store.put(data, content_type="application/json", prefix="reports")
    )

    with session_scope() as s:
        art = Artifact(
            id=uuid.uuid4(),
            case_id=case_id,
            kind="report_json",
            object_key=obj.key,
            sha256=obj.sha256,
            size=obj.size,
            mime=obj.content_type,
        )
        s.add(art)
        s.flush()
        report = Report(
            id=uuid.uuid4(),
            case_id=case_id,
            score_id=score_id,
            artifact_id=art.id,
            sha256=hashlib.sha256(data).hexdigest(),
        )
        s.add(report)
        audit.append(
            s,
            "report.built",
            {
                "report_id": str(report.id),
                "artifact_id": str(art.id),
                "sha256": report.sha256,
                "bytes": obj.size,
            },
            case_id=case_id,
        )
        nxt = state.advance(s, case_id, STAGE)
    if nxt:
        state.enqueue_stage(providers.queue, case_id, nxt)
