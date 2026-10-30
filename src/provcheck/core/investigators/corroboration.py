"""Corroboration investigator.

Looks for independent credible outlets reporting the same event. One wire
service copied by 20 outlets still counts as one source — we group hits by
registrable domain (via tldextract) and intersect with the curated reputation
list.

Signals:
  * ``corroboration.independent_outlets``  — ≥2 distinct reputable domains
    cover the same claim/title. Supports credibility.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from provcheck.core.db import audit
from provcheck.core.db.models import Capture, EvidenceItem, UnverifiedItem
from provcheck.core.db.session import session_scope
from provcheck.core.interfaces import NotConfigured, ProviderError, ProviderUnavailable
from provcheck.core.investigators.text_helpers import (
    distinctive_sentences,
    independent_registrable_domains,
)
from provcheck.core.pipeline import orchestrator
from provcheck.core.providers.registrable import registrable_domain
from provcheck.core.providers.registry import Providers

log = logging.getLogger(__name__)
INVESTIGATOR = "corroboration"


async def _query(providers: Providers, query: str) -> list[str]:
    """Return the URLs of the first page of a news-category search."""
    try:
        results = await providers.search.search(query, categories=("news",), max_results=15)
    except NotConfigured:
        raise
    except (ProviderUnavailable, ProviderError):
        raise
    return [r.url for r in results]


async def _run_async(
    case_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    queries: list[str],
    exclude_domain: str | None,
    providers: Providers,
) -> tuple[list[EvidenceItem], list[UnverifiedItem]]:
    ev: list[EvidenceItem] = []
    un: list[UnverifiedItem] = []
    all_urls: list[str] = []
    for q in queries:
        try:
            urls = await _query(providers, q)
        except NotConfigured as e:
            un.append(_unv(case_id, run_id, q, "not_configured", str(e)))
            return ev, un
        except (ProviderUnavailable, ProviderError) as e:
            un.append(_unv(case_id, run_id, q, "provider_unavailable", f"{type(e).__name__}: {e}"))
            return ev, un
        all_urls.extend(urls)

    domains = independent_registrable_domains(all_urls)
    if exclude_domain:
        domains = [d for d in domains if d != exclude_domain]

    reputable = [d for d in domains if providers.sources.is_reputable(d) is not None]

    if len(reputable) >= 2:
        strength = 0.9 if len(reputable) >= 4 else 0.7
        ev.append(
            EvidenceItem(
                id=uuid.uuid4(),
                case_id=case_id,
                run_id=run_id,
                investigator=INVESTIGATOR,
                signal="corroboration.independent_outlets",
                finding=(
                    f"{len(reputable)} independent reputable outlets reporting on this: "
                    + ", ".join(reputable[:5])
                ),
                source_url=None,
                direction="supports",
                strength=strength,
                confidence=0.8,
                category_hint="corroborated",
                details={
                    "reputable_domains": reputable,
                    "all_domains": domains[:30],
                    "total_results": len(all_urls),
                },
            )
        )
    else:
        un.append(
            _unv(
                case_id,
                run_id,
                "corroboration",
                "no_results",
                (
                    f"no independent reputable outlets found "
                    f"(all distinct domains: {len(domains)}, "
                    f"reputable: {len(reputable)}, "
                    f"queries: {len(queries)})"
                ),
            )
        )
    un.append(
        _unv(
            case_id,
            run_id,
            "corroboration",
            "unsupported_platform",
            "Social-media echo and coordinated-amplification signals need platform APIs.",
        )
    )
    return ev, un


def run_corroboration(case_id: uuid.UUID, run_id: uuid.UUID, providers: Providers) -> None:
    run = orchestrator.start_run(case_id, run_id)
    if run is None:
        return

    with session_scope() as s:
        cap = (
            s.query(Capture)
            .filter(Capture.case_id == case_id)
            .order_by(Capture.fetched_at.desc())
            .first()
        )
        if cap is None:
            orchestrator.finish_run(case_id, run_id, status="skipped", counts={"reason": 0})
            return
        title = cap.title
        final_url = cap.final_url
        text_art_id = cap.text_artifact_id

    text = ""
    if text_art_id is not None:
        with session_scope() as s:
            from provcheck.core.db.models import Artifact

            art = s.get(Artifact, text_art_id)
            key = art.object_key if art else None
        if key:
            try:
                data = asyncio.run(providers.object_store.get(key))
                text = data.decode("utf-8", errors="replace")
            except Exception as e:
                log.info("corroboration: failed to load text artifact: %s", e)

    queries: list[str] = []
    if title:
        queries.append(title.strip())
    queries.extend(distinctive_sentences(text, limit=1))
    # Dedupe while preserving order; cap at 2 queries.
    queries = list(dict.fromkeys(queries))[:2]

    exclude = registrable_domain(final_url)

    if not queries:
        orchestrator.finish_run(
            case_id,
            run_id,
            status="skipped",
            counts={"reason": 0},
            providers_used={"search": providers.search.name},
        )
        return

    try:
        ev, un = asyncio.run(
            _run_async(
                case_id,
                run_id,
                queries=queries,
                exclude_domain=exclude,
                providers=providers,
            )
        )
    except Exception as e:
        log.exception("corroboration crashed: case=%s", case_id)
        orchestrator.finish_run(case_id, run_id, status="failed", error=f"{type(e).__name__}: {e}")
        return

    with session_scope() as s:
        for e_item in ev:
            s.add(e_item)
        for u_item in un:
            s.add(u_item)
        audit.append(
            s,
            "corroboration.wrote",
            {"evidence": len(ev), "unverified": len(un), "queries": len(queries)},
            case_id=case_id,
        )

    orchestrator.finish_run(
        case_id,
        run_id,
        status="done",
        providers_used={"search": providers.search.name},
        counts={"evidence": len(ev), "unverified": len(un)},
    )


def _unv(
    case_id: uuid.UUID, run_id: uuid.UUID, subject: str, reason: str, detail: str
) -> UnverifiedItem:
    return UnverifiedItem(
        id=uuid.uuid4(),
        case_id=case_id,
        run_id=run_id,
        investigator=INVESTIGATOR,
        subject=subject,
        reason=reason,
        detail=detail,
    )
