"""Provenance investigator.

Finds where the content (or something like it) appeared earliest.

Signals:
  * ``provenance.earliest_appearance``   — SearXNG exact-phrase hit older than
    the capture's ``claimed_published_at``.
  * ``provenance.archive_earliest``       — oldest Wayback CDX capture of the URL.
  * ``provenance.earlier_copy``           — local pHash reverse-image match on a
    previously-captured page with a different URL.

Platform-coordination signals are listed as ``unsupported_platform``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any

from provcheck.core.db import audit
from provcheck.core.db.models import Artifact, Capture, EvidenceItem, UnverifiedItem
from provcheck.core.db.session import session_scope
from provcheck.core.interfaces import NotConfigured, ProviderError, ProviderUnavailable
from provcheck.core.investigators.text_helpers import distinctive_sentences
from provcheck.core.pipeline import orchestrator
from provcheck.core.providers.registry import Providers

log = logging.getLogger(__name__)
INVESTIGATOR = "provenance"


async def _run_async(
    case_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    sentences: list[str],
    final_url: str,
    phashes: list[str],
    claimed_published_at: str | None,
    providers: Providers,
) -> tuple[list[EvidenceItem], list[UnverifiedItem]]:
    ev: list[EvidenceItem] = []
    un: list[UnverifiedItem] = []

    # --- SearXNG exact-phrase search for distinctive sentences --------------
    earliest_hit: tuple[datetime, Any] | None = None
    for sentence in sentences:
        quoted = f'"{sentence}"'
        try:
            results = await providers.search.search(quoted, max_results=10)
        except NotConfigured as e:
            un.append(_unv(case_id, run_id, "earliest_sentence", "not_configured", str(e)))
            break
        except (ProviderUnavailable, ProviderError) as e:
            un.append(
                _unv(
                    case_id,
                    run_id,
                    "earliest_sentence",
                    "provider_unavailable",
                    f"{type(e).__name__}: {e}",
                )
            )
            break
        for r in results:
            if r.published_at is None:
                continue
            if earliest_hit is None or r.published_at < earliest_hit[0]:
                earliest_hit = (r.published_at, r)
    if earliest_hit is not None:
        when, hit = earliest_hit
        own_date = _parse_iso_date(claimed_published_at)
        if own_date and when.replace(tzinfo=None) < own_date.replace(tzinfo=None):
            ev.append(
                EvidenceItem(
                    id=uuid.uuid4(),
                    case_id=case_id,
                    run_id=run_id,
                    investigator=INVESTIGATOR,
                    signal="provenance.earliest_appearance",
                    finding=(
                        f"An exact-phrase copy was published on {when.date().isoformat()} "
                        f"— earlier than the article's claimed date "
                        f"{own_date.date().isoformat()}."
                    ),
                    source_url=hit.url,
                    source_title=hit.title,
                    source_published_at=when,
                    direction="undermines",
                    strength=0.6,
                    confidence=0.6,
                    category_hint="misleading_context",
                    details={"snippet": hit.snippet[:300]},
                )
            )

    # --- Wayback CDX history --------------------------------------------------
    try:
        history = await providers.archive.history(final_url, limit=25) if final_url else []
    except NotConfigured as e:
        history = []
        un.append(_unv(case_id, run_id, "archive_history", "not_configured", str(e)))
    except (ProviderUnavailable, ProviderError) as e:
        history = []
        un.append(
            _unv(
                case_id,
                run_id,
                "archive_history",
                "provider_unavailable",
                f"{type(e).__name__}: {e}",
            )
        )
    if history:
        earliest = min(history, key=lambda h: h.captured_at)
        ev.append(
            EvidenceItem(
                id=uuid.uuid4(),
                case_id=case_id,
                run_id=run_id,
                investigator=INVESTIGATOR,
                signal="provenance.archive_earliest",
                finding=(
                    f"Earliest Wayback capture of this URL is "
                    f"{earliest.captured_at.date().isoformat()} "
                    f"({len(history)} captures total)."
                ),
                source_url=earliest.archive_url,
                source_title=None,
                source_published_at=earliest.captured_at,
                direction="neutral",
                strength=0.4,
                confidence=0.8,
                category_hint=None,
                details={"total_captures": len(history)},
            )
        )

    # --- Local pHash reverse image search ------------------------------------
    if phashes:
        try:
            matches = await providers.reverse_image.search(phashes, max_results=10)
        except NotConfigured as e:
            matches = []
            un.append(_unv(case_id, run_id, "reverse_image", "not_configured", str(e)))
        except (ProviderUnavailable, ProviderError) as e:
            matches = []
            un.append(
                _unv(
                    case_id,
                    run_id,
                    "reverse_image",
                    "provider_unavailable",
                    f"{type(e).__name__}: {e}",
                )
            )
        if matches:
            for m in matches[:5]:
                ev.append(
                    EvidenceItem(
                        id=uuid.uuid4(),
                        case_id=case_id,
                        run_id=run_id,
                        investigator=INVESTIGATOR,
                        signal="provenance.earlier_copy",
                        finding=(
                            "A visually-similar image was previously captured on "
                            f"{m.page_url} ("
                            f"{m.first_seen.date().isoformat() if m.first_seen else '?'})."
                        ),
                        source_url=m.page_url,
                        source_published_at=m.first_seen,
                        direction="undermines",
                        strength=0.5,
                        confidence=float(m.similarity or 0.5),
                        category_hint="misleading_context",
                        details={"phash": m.phash, "similarity": m.similarity},
                    )
                )
        else:
            un.append(
                _unv(
                    case_id,
                    run_id,
                    "reverse_image",
                    "no_results",
                    "no visually-similar image seen in prior captures",
                )
            )
    else:
        un.append(
            _unv(
                case_id,
                run_id,
                "reverse_image",
                "no_results",
                "capture produced no indexable images",
            )
        )

    # --- Account / coordination signals (unsupported) -------------------------
    un.append(
        _unv(
            case_id,
            run_id,
            "account_coordination",
            "unsupported_platform",
            (
                "Posting-pattern and coordination signals require platform APIs "
                "(e.g. Twitter/X, Facebook Graph) that are not configured."
            ),
        )
    )
    return ev, un


def run_provenance(case_id: uuid.UUID, run_id: uuid.UUID, providers: Providers) -> None:
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
            orchestrator.finish_run(
                case_id,
                run_id,
                status="skipped",
                providers_used={"search": providers.search.name},
                counts={"reason": 0},
            )
            return
        final_url = cap.final_url
        claimed = cap.claimed_published_at
        text_art_id = cap.text_artifact_id
        case_id_val = cap.case_id
        image_arts = (
            s.query(Artifact)
            .filter(
                Artifact.case_id == case_id_val,
                Artifact.kind.in_(("screenshot", "media")),
            )
            .all()
        )
        phashes = [a.phash for a in image_arts if a.phash]

    text = ""
    if text_art_id is not None:
        with session_scope() as s:
            art = s.get(Artifact, text_art_id)
            key = art.object_key if art else None
        if key:
            try:
                data = asyncio.run(providers.object_store.get(key))
                text = data.decode("utf-8", errors="replace")
            except Exception as e:
                log.info("provenance: failed to load text artifact: %s", e)

    sentences = distinctive_sentences(text, limit=2) if text else []

    try:
        ev, un = asyncio.run(
            _run_async(
                case_id,
                run_id,
                sentences=sentences,
                final_url=final_url,
                phashes=phashes,
                claimed_published_at=claimed,
                providers=providers,
            )
        )
    except Exception as e:
        log.exception("provenance crashed: case=%s", case_id)
        orchestrator.finish_run(case_id, run_id, status="failed", error=f"{type(e).__name__}: {e}")
        return

    with session_scope() as s:
        for e_item in ev:
            s.add(e_item)
        for u_item in un:
            s.add(u_item)
        audit.append(
            s,
            "provenance.wrote",
            {
                "evidence": len(ev),
                "unverified": len(un),
                "sentences": len(sentences),
                "phashes": len(phashes),
            },
            case_id=case_id,
        )

    orchestrator.finish_run(
        case_id,
        run_id,
        status="done",
        providers_used={
            "search": providers.search.name,
            "archive": providers.archive.name,
            "reverse_image": providers.reverse_image.name,
        },
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


def _parse_iso_date(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
