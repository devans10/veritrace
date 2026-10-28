"""Report builder.

Assembles a single JSON document from the DB rows of a scored case. The JSON
is stored as an immutable ``report_json`` artifact and linked to the Report
row — the UI renders a view of it, but the JSON is the source of truth.

Field names are stable (versioned via ``report.schema_version``) so API
consumers can rely on them. Internal fields that must not appear in the UI
(``raw_logit``, ``risk``) are deliberately omitted — the plan is band-only
until calibration against labeled data.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from provcheck.core.db.models import (
    Artifact,
    Capture,
    Case,
    Claim,
    EvidenceItem,
    Score,
    UnverifiedItem,
)
from provcheck.core.scoring import ScoreResult

SCHEMA_VERSION = "report-schema-v1"
DISCLAIMER = (
    "This is a credibility assessment with an evidence trail, not a verdict. "
    "Absence of evidence is not evidence of fabrication; always inspect the "
    "evidence trail and the could-not-verify list."
)


def build_report(
    *,
    session: Session,
    case: Case,
    score: Score,
    score_result: ScoreResult,
) -> dict[str, Any]:
    captures = list(
        session.execute(
            select(Capture).where(Capture.case_id == case.id).order_by(Capture.fetched_at)
        ).scalars()
    )
    claims = list(
        session.execute(
            select(Claim).where(Claim.case_id == case.id).order_by(Claim.ordinal)
        ).scalars()
    )
    evidence = list(
        session.execute(
            select(EvidenceItem)
            .where(EvidenceItem.case_id == case.id)
            .order_by(EvidenceItem.investigator, EvidenceItem.id)
        ).scalars()
    )
    unverified = list(
        session.execute(
            select(UnverifiedItem)
            .where(UnverifiedItem.case_id == case.id)
            .order_by(UnverifiedItem.investigator, UnverifiedItem.id)
        ).scalars()
    )
    all_artifacts: dict[uuid.UUID, Artifact] = {
        a.id: a
        for a in session.execute(select(Artifact).where(Artifact.case_id == case.id)).scalars()
    }

    by_investigator: dict[str, list[dict[str, Any]]] = {}
    for ev in evidence:
        by_investigator.setdefault(ev.investigator, []).append(
            {
                "id": str(ev.id),
                "signal": ev.signal,
                "finding": ev.finding,
                "direction": ev.direction,
                "strength": float(ev.strength),
                "confidence": float(ev.confidence),
                "category_hint": ev.category_hint,
                "source_url": ev.source_url,
                "source_title": ev.source_title,
                "source_published_at": (
                    ev.source_published_at.isoformat() if ev.source_published_at else None
                ),
                "retrieved_at": ev.retrieved_at.isoformat() if ev.retrieved_at else None,
                "details": ev.details or {},
            }
        )
    unverified_by_investigator: dict[str, list[dict[str, Any]]] = {}
    for un in unverified:
        unverified_by_investigator.setdefault(un.investigator, []).append(
            {
                "subject": un.subject,
                "reason": un.reason,
                "detail": un.detail,
            }
        )

    capture_blocks = []
    for c in captures:
        arts = []
        for aid in (c.html_artifact_id, c.screenshot_artifact_id, c.text_artifact_id):
            if aid and aid in all_artifacts:
                a = all_artifacts[aid]
                arts.append(
                    {
                        "kind": a.kind,
                        "sha256": a.sha256,
                        "size": a.size,
                        "mime": a.mime,
                        "href": f"/v1/cases/{case.id}/artifacts/{a.id}",
                    }
                )
        capture_blocks.append(
            {
                "requested_url": c.requested_url,
                "final_url": c.final_url,
                "redirect_chain": c.redirect_chain,
                "http_status": c.http_status,
                "fetched_at": c.fetched_at.isoformat() if c.fetched_at else None,
                "title": c.title,
                "claimed_published_at": c.claimed_published_at,
                "text_sha256": c.text_sha256,
                "truncated": c.truncated,
                "blocked_requests": len(c.blocked_requests or []),
                "archive": {
                    "status": c.archive_status,
                    "url": c.archive_url,
                    "error": c.archive_error,
                },
                "artifacts": arts,
            }
        )

    ela_artifacts = [
        {
            "sha256": a.sha256,
            "size": a.size,
            "href": f"/v1/cases/{case.id}/artifacts/{a.id}",
        }
        for a in all_artifacts.values()
        if a.kind == "ela"
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "disclaimer": DISCLAIMER,
        "case": {
            "id": str(case.id),
            "input_type": case.input_type,
            "input_url": case.input_url,
            "normalized_url": case.normalized_url,
            "created_at": case.created_at.isoformat() if case.created_at else None,
        },
        "assessment": {
            "band": score.band,
            "confidence_level": score.confidence_level,
            "scorer_version": score.scorer_version,
            "config_sha256": score.config_sha256,
        },
        "coverage": score_result.coverage,
        "categories": [
            {
                "category": cat.category,
                "total_abs": cat.total_abs,
                "evidence_ids": [str(eid) for eid in cat.evidence_ids],
            }
            for cat in score_result.category_breakdown
        ],
        "contributions": [
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
            for c in score_result.contributions
        ],
        "evidence_by_investigator": by_investigator,
        "unverified_by_investigator": unverified_by_investigator,
        "claims": [
            {
                "ordinal": cl.ordinal,
                "text": cl.text,
                "source_span": cl.source_span,
                "claim_type": cl.claim_type,
                "checkworthiness": cl.checkworthiness,
            }
            for cl in claims
        ],
        "captures": capture_blocks,
        "ela_artifacts": ela_artifacts,
    }
