"""Scoring stage.

Runs after ``advance_investigation`` moves the case to ``scoring``. Reads all
evidence rows, computes a :class:`ScoreResult`, writes a ``scores`` row, builds
the report JSON, stores it as a ``report_json`` artifact, writes a ``reports``
row, and advances the case to ``complete``.

**Retry safety.** This stage touches the DB and the object store across three
transactions and one external write. A worker killed mid-stage leaves the
case in ``scoring`` and must be picked up by a retry — so each sub-step is
idempotent:

* The Score row is covered by ``UNIQUE (case_id)`` (migration 0004). If a
  prior crash wrote one, the retry loads it and rebuilds a ``ScoreResult``
  from the persisted payload rather than recomputing against possibly-
  mutated evidence rows.
* The Report row is loaded by ``case_id`` and skipped if present. A duplicate
  ``report_json`` artifact write is harmless (content-addressed in the
  object store) but a duplicate Report row would confuse the API's
  "latest report" lookup, so we guard it explicitly.
* ``state.advance`` is already idempotent under the Case row lock.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from typing import Any

from sqlalchemy import select

from provcheck.core.db import audit
from provcheck.core.db.models import Artifact, Case, EvidenceItem, Report, Score
from provcheck.core.db.session import session_scope
from provcheck.core.pipeline import state
from provcheck.core.providers.registry import Providers
from provcheck.core.report.builder import build_report
from provcheck.core.scoring import (
    CategoryBreakdown,
    Contribution,
    ScoreResult,
    default_scoring_config,
    score_case,
)

log = logging.getLogger(__name__)
STAGE = "scoring"


def _canonical_json(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _rehydrate_result(score: Score) -> ScoreResult:
    """Rebuild a ScoreResult from a persisted Score row.

    Used by the retry path when a prior crash wrote the Score but not the
    Report. Preserves the exact numeric breakdown the first run computed —
    we deliberately do NOT re-score, in case evidence rows mutated between
    the first run and the retry (shouldn't happen in v0.1, but the whole
    point of this path is to be defensive about crashes).
    """
    contribs = [
        Contribution(
            evidence_id=uuid.UUID(c["evidence_id"]),
            signal=str(c["signal"]),
            family=str(c["family"]),
            category=(str(c["category"]) if c.get("category") else None),
            direction=str(c["direction"]),  # type: ignore[arg-type]
            raw=float(c["raw"]),
            value=float(c["value"]),
            weight=float(c["weight"]),
            cap=float(c["cap"]),
        )
        for c in (score.contributions or [])
    ]
    breakdown = [
        CategoryBreakdown(
            category=str(cat["category"]),
            total_abs=float(cat["total_abs"]),
            evidence_ids=[uuid.UUID(eid) for eid in (cat.get("evidence_ids") or [])],
        )
        for cat in (score.category_breakdown or [])
    ]
    coverage = dict(score.coverage or {})
    return ScoreResult(
        scorer_version=score.scorer_version,
        config_sha256=score.config_sha256,
        band=score.band,  # type: ignore[arg-type]
        confidence_level=score.confidence_level,  # type: ignore[arg-type]
        raw_logit=float(score.raw_logit),
        risk=0.0,  # internal-only; not stored, not read back by the report builder
        coverage=coverage,
        category_breakdown=breakdown,
        contributions=contribs,
        unknown_signals=[],  # also not persisted; absent on resume
    )


def _score_row(case_id: uuid.UUID, result: ScoreResult) -> Score:
    return Score(
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


def _audit_payload(result: ScoreResult, *, resumed: bool) -> dict[str, Any]:
    return {
        "scorer_version": result.scorer_version,
        "config_sha256": result.config_sha256,
        "band": result.band,
        "confidence_level": result.confidence_level,
        "contributions": len(result.contributions),
        "unknown_signals": result.unknown_signals,
        "resumed": resumed,
    }


def run_score(case_id: uuid.UUID, providers: Providers) -> None:
    config = default_scoring_config()
    result: ScoreResult
    score_id: uuid.UUID
    resumed = False

    with session_scope() as s:
        case = s.get(Case, case_id, with_for_update=True)
        if case is None or case.status != STAGE:
            log.info("score skipped: case=%s status=%s", case_id, case and case.status)
            return
        audit.append(s, "stage.started", {"stage": STAGE}, case_id=case_id)
        existing_score = s.execute(
            select(Score).where(Score.case_id == case_id)
        ).scalar_one_or_none()
        if existing_score is not None:
            # Prior crash left the Score behind. Resume from it.
            resumed = True
            score_id = existing_score.id
            result = _rehydrate_result(existing_score)
            evidence: list[EvidenceItem] | None = None
            log.info("score: resuming from existing score=%s", score_id)
        else:
            evidence = list(s.query(EvidenceItem).filter(EvidenceItem.case_id == case_id).all())
            for e in evidence:
                s.expunge(e)
            score_id = uuid.UUID(int=0)  # overwritten below

    if not resumed:
        assert evidence is not None
        try:
            result = score_case(evidence, config)
        except Exception as e:
            log.exception("score crashed: case=%s", case_id)
            with session_scope() as s:
                state.fail_case(s, case_id, STAGE, "error", f"{type(e).__name__}: {e}")
            return

        with session_scope() as s:
            score = _score_row(case_id, result)
            s.add(score)
            audit.append(
                s, "score.computed", _audit_payload(result, resumed=False), case_id=case_id
            )
            s.flush()
            score_id = score.id

    # Skip the report build + persist if a Report already exists from a prior
    # crashed run. advance() is idempotent and takes us to complete regardless.
    with session_scope() as s:
        existing_report = s.execute(
            select(Report).where(Report.case_id == case_id)
        ).scalar_one_or_none()
    if existing_report is None:
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
                    "resumed": resumed,
                },
                case_id=case_id,
            )
    else:
        log.info("score: report already built; skipping to advance")
        with session_scope() as s:
            audit.append(
                s,
                "report.resumed",
                {"report_id": str(existing_report.id)},
                case_id=case_id,
            )

    with session_scope() as s:
        nxt = state.advance(s, case_id, STAGE)
    if nxt:
        state.enqueue_stage(providers.queue, case_id, nxt)
