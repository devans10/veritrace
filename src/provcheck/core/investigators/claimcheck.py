"""Claim-check investigator.

For each extracted claim:
  1. Query Google Fact Check (if configured) with the claim text.
  2. For each hit, map the textual rating via the conservative lexicon. Unknown
     ratings → neutral direction + an entry in the unverified list, never
     evidence.
  3. Report provider outages as unverified entries, never silent gaps.

SearXNG restricted-domain search and inline ClaimReview JSON-LD parsing are
listed in the step 2 build plan; this slice ships the Google side so the
pipeline is end-to-end testable with a FakeFactCheck under the inline queue.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from provcheck.core.db import audit
from provcheck.core.db.models import Claim, EvidenceItem, UnverifiedItem
from provcheck.core.db.session import session_scope
from provcheck.core.interfaces import (
    ClaimReviewHit,
    FactCheckProvider,
    NotConfigured,
    ProviderError,
    ProviderUnavailable,
)
from provcheck.core.investigators.rating_lexicon import map_rating
from provcheck.core.pipeline import orchestrator
from provcheck.core.providers.registry import Providers

log = logging.getLogger(__name__)
INVESTIGATOR = "claimcheck"


def _signal_for_direction(direction: str) -> str:
    return {
        "undermines": "factcheck.claimreview_false",
        "supports": "factcheck.claimreview_true",
        "neutral": "factcheck.claimreview_mixed",
    }.get(direction, "factcheck.claimreview_mixed")


def _hit_to_evidence(
    case_id: uuid.UUID, run_id: uuid.UUID, claim_id: uuid.UUID, hit: ClaimReviewHit
) -> tuple[EvidenceItem | None, UnverifiedItem | None]:
    """Map a ClaimReview hit to evidence (if direction maps) or an unverified entry."""
    mapping = map_rating(hit.textual_rating)
    details = {
        "textual_rating": hit.textual_rating,
        "publisher": hit.review_publisher,
        "publisher_site": hit.review_publisher_site,
        "claim_text": hit.claim_text,
        "claim_date": str(hit.claim_date) if hit.claim_date else None,
        "language": hit.language_code,
    }
    if mapping.direction == "neutral" and mapping.strength == 0:
        unv = UnverifiedItem(
            id=uuid.uuid4(),
            case_id=case_id,
            run_id=run_id,
            claim_id=claim_id,
            investigator=INVESTIGATOR,
            subject=hit.claim_text or hit.review_title or hit.review_url,
            reason="no_results",
            detail=f"unmapped rating: {hit.textual_rating!r} ({hit.review_url})",
        )
        return None, unv
    ev = EvidenceItem(
        id=uuid.uuid4(),
        case_id=case_id,
        run_id=run_id,
        claim_id=claim_id,
        investigator=INVESTIGATOR,
        signal=_signal_for_direction(mapping.direction),
        finding=(f"{hit.review_publisher or 'fact-check'} rated this {hit.textual_rating!r}"),
        source_url=hit.review_url,
        source_title=hit.review_title,
        source_published_at=None,  # ClaimReview review_date is a date, not datetime
        direction=mapping.direction,
        strength=float(mapping.strength),
        confidence=0.8,
        category_hint=None,
        details=details,
    )
    return ev, None


async def _run_async(
    case_id: uuid.UUID,
    run_id: uuid.UUID,
    claims: list[tuple[uuid.UUID, str]],
    fc: FactCheckProvider,
) -> tuple[list[EvidenceItem], list[UnverifiedItem]]:
    evidence: list[EvidenceItem] = []
    unverified: list[UnverifiedItem] = []
    for claim_id, text in claims:
        try:
            hits = await fc.search(text, max_results=5)
        except NotConfigured as e:
            unverified.append(
                UnverifiedItem(
                    id=uuid.uuid4(),
                    case_id=case_id,
                    run_id=run_id,
                    claim_id=claim_id,
                    investigator=INVESTIGATOR,
                    subject=text[:500],
                    reason="not_configured",
                    detail=str(e),
                )
            )
            continue
        except (ProviderUnavailable, ProviderError) as e:
            unverified.append(
                UnverifiedItem(
                    id=uuid.uuid4(),
                    case_id=case_id,
                    run_id=run_id,
                    claim_id=claim_id,
                    investigator=INVESTIGATOR,
                    subject=text[:500],
                    reason="provider_unavailable",
                    detail=f"{type(e).__name__}: {e}",
                )
            )
            continue
        if not hits:
            unverified.append(
                UnverifiedItem(
                    id=uuid.uuid4(),
                    case_id=case_id,
                    run_id=run_id,
                    claim_id=claim_id,
                    investigator=INVESTIGATOR,
                    subject=text[:500],
                    reason="no_results",
                    detail="no ClaimReview hits",
                )
            )
            continue
        for hit in hits:
            ev, unv = _hit_to_evidence(case_id, run_id, claim_id, hit)
            if ev is not None:
                evidence.append(ev)
            if unv is not None:
                unverified.append(unv)
    return evidence, unverified


def run_claimcheck(case_id: uuid.UUID, run_id: uuid.UUID, providers: Providers) -> None:
    import asyncio

    run = orchestrator.start_run(case_id, run_id)
    if run is None:
        return

    with session_scope() as s:
        rows: list[tuple[uuid.UUID, str]] = [
            (c.id, c.text)
            for c in s.query(Claim).filter(Claim.case_id == case_id).order_by(Claim.ordinal).all()
        ]

    if not rows:
        orchestrator.finish_run(
            case_id, run_id, status="skipped", counts={"claims": 0}, providers_used={}
        )
        return

    try:
        evidence, unverified = asyncio.run(_run_async(case_id, run_id, rows, providers.factcheck))
    except Exception as e:
        log.exception("claimcheck crashed: case=%s", case_id)
        orchestrator.finish_run(case_id, run_id, status="failed", error=f"{type(e).__name__}: {e}")
        return

    with session_scope() as s:
        for ev in evidence:
            s.add(ev)
        for unv in unverified:
            s.add(unv)
        audit.append(
            s,
            "claimcheck.wrote",
            {
                "evidence": len(evidence),
                "unverified": len(unverified),
                "factcheck_provider": providers.factcheck.name,
            },
            case_id=case_id,
        )

    orchestrator.finish_run(
        case_id,
        run_id,
        status="done",
        providers_used={"factcheck": providers.factcheck.name},
        counts={"evidence": len(evidence), "unverified": len(unverified)},
    )


def _asyncio_run_unused(coro: Any) -> Any:
    """Only here to keep mypy happy about the sync entrypoint; unused."""
    import asyncio

    return asyncio.run(coro)
