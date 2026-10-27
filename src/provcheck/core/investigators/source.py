"""Source investigator.

Assesses the source itself, not what it says:

Signals:
  * ``source.satire_domain``       — the final URL's registrable domain is on
    the curated satire list. Sets category_hint=satire.
  * ``source.reputable_outlet``    — on the curated reputation list.
  * ``source.domain_age_lt_30d``   — RDAP registration date < 30 days ago.
  * ``source.domain_age_established`` — RDAP registration date > 2 years ago.

Account / coordination signals are reported as ``unsupported_platform``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlparse

from provcheck.core.db import audit
from provcheck.core.db.models import Capture, EvidenceItem, UnverifiedItem
from provcheck.core.db.session import session_scope
from provcheck.core.interfaces import DomainInfo, NotConfigured, ProviderError, ProviderUnavailable
from provcheck.core.pipeline import orchestrator
from provcheck.core.providers.registrable import registrable_domain
from provcheck.core.providers.registry import Providers

log = logging.getLogger(__name__)
INVESTIGATOR = "source"


async def _run_async(
    case_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    final_url: str,
    providers: Providers,
) -> tuple[list[EvidenceItem], list[UnverifiedItem]]:
    ev: list[EvidenceItem] = []
    un: list[UnverifiedItem] = []
    reg = registrable_domain(_host(final_url))
    if reg is None:
        un.append(
            _unv(
                case_id,
                run_id,
                final_url,
                "error",
                "could not extract a registrable domain from the final URL",
            )
        )
        return ev, un

    # --- Seed-list matches ---------------------------------------------------
    satire = providers.sources.is_satire(reg)
    if satire is not None:
        ev.append(
            EvidenceItem(
                id=uuid.uuid4(),
                case_id=case_id,
                run_id=run_id,
                investigator=INVESTIGATOR,
                signal="source.satire_domain",
                finding=f"{reg} is a known satirical publication.",
                source_url=None,
                direction="neutral",
                strength=0.6,
                confidence=0.9,
                category_hint="satire",
                details={"basis": satire.source},
            )
        )

    rep = providers.sources.is_reputable(reg)
    if rep is not None:
        ev.append(
            EvidenceItem(
                id=uuid.uuid4(),
                case_id=case_id,
                run_id=run_id,
                investigator=INVESTIGATOR,
                signal="source.reputable_outlet",
                finding=f"{reg} is on the reputation list ({rep.tier or 'listed'}).",
                source_url=None,
                direction="supports",
                strength=0.5,
                confidence=0.8,
                category_hint="corroborated",
                details={"basis": rep.source, "tier": rep.tier},
            )
        )

    # --- RDAP ----------------------------------------------------------------
    info: DomainInfo | None = None
    try:
        info = await providers.domaininfo.lookup(reg)
    except NotConfigured as e:
        un.append(_unv(case_id, run_id, reg, "not_configured", str(e)))
    except (ProviderUnavailable, ProviderError) as e:
        un.append(_unv(case_id, run_id, reg, "provider_unavailable", f"{type(e).__name__}: {e}"))
    if info is not None and info.registered_on is not None:
        age_days = (date.today() - info.registered_on).days
        if age_days < 30:
            ev.append(
                EvidenceItem(
                    id=uuid.uuid4(),
                    case_id=case_id,
                    run_id=run_id,
                    investigator=INVESTIGATOR,
                    signal="source.domain_age_lt_30d",
                    finding=(
                        f"{reg} was registered {age_days} days ago "
                        f"({info.registered_on.isoformat()})."
                    ),
                    source_url=None,
                    direction="undermines",
                    strength=0.7,
                    confidence=0.8,
                    category_hint="fabricated",
                    details={
                        "registered_on": info.registered_on.isoformat(),
                        "age_days": age_days,
                        "registrar": info.registrar,
                    },
                )
            )
        elif age_days > 365 * 2:
            ev.append(
                EvidenceItem(
                    id=uuid.uuid4(),
                    case_id=case_id,
                    run_id=run_id,
                    investigator=INVESTIGATOR,
                    signal="source.domain_age_established",
                    finding=(
                        f"{reg} has been registered for "
                        f"{age_days // 365} years "
                        f"(since {info.registered_on.isoformat()})."
                    ),
                    source_url=None,
                    direction="supports",
                    strength=0.3,
                    confidence=0.7,
                    details={
                        "registered_on": info.registered_on.isoformat(),
                        "age_days": age_days,
                        "registrar": info.registrar,
                    },
                )
            )
    elif info is not None and info.registered_on is None:
        un.append(
            _unv(
                case_id,
                run_id,
                reg,
                "no_results",
                "RDAP record found but registration date is missing",
            )
        )

    # --- Account / coordination (unsupported) --------------------------------
    un.append(
        _unv(
            case_id,
            run_id,
            reg,
            "unsupported_platform",
            (
                "Account age, posting history, and coordination signals require "
                "platform APIs that are not configured in v0.1."
            ),
        )
    )
    return ev, un


def run_source(case_id: uuid.UUID, run_id: uuid.UUID, providers: Providers) -> None:
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
        final_url = cap.final_url if cap else ""

    if not final_url:
        orchestrator.finish_run(case_id, run_id, status="skipped", counts={"reason": 0})
        return

    try:
        ev, un = asyncio.run(_run_async(case_id, run_id, final_url=final_url, providers=providers))
    except Exception as e:
        log.exception("source crashed: case=%s", case_id)
        orchestrator.finish_run(case_id, run_id, status="failed", error=f"{type(e).__name__}: {e}")
        return

    with session_scope() as s:
        for e_item in ev:
            s.add(e_item)
        for u_item in un:
            s.add(u_item)
        audit.append(
            s,
            "source.wrote",
            {"evidence": len(ev), "unverified": len(un)},
            case_id=case_id,
        )

    orchestrator.finish_run(
        case_id,
        run_id,
        status="done",
        providers_used={"domaininfo": providers.domaininfo.name},
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


def _host(url: str) -> str:
    try:
        p = urlparse(url)
        return p.hostname or url
    except ValueError:
        return url


# ``datetime``, ``UTC``, ``timedelta`` are imported for future-ready signals
# (e.g. account-age cutoffs) — currently unused.
_ = (datetime, UTC, timedelta)
