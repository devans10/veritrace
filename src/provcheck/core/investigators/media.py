"""Media forensics investigator.

Signals (per media item):
  * ``media.c2pa_valid``          — strong supports when manifest validates.
  * ``media.c2pa_invalid``        — strong undermines when manifest is present but invalid.
  * ``media.exif_date_mismatch``  — the article's claimed date disagrees with EXIF's
    DateTimeOriginal by a wide margin.
  * ``media.ela``                 — informational, weight 0; a diff artifact is stored.

A missing EXIF, missing C2PA, or disabled detector produces an ``unverified``
entry — never silent gaps.

For URL cases, the investigator parses the captured HTML and downloads up to
``settings.media_max_images_per_case`` images via SafeFetcher (through the
egress proxy; private-IP destinations are rejected). For upload cases it
operates on the single uploaded artifact.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any

from provcheck.core.db import audit
from provcheck.core.db.models import Artifact, Capture, Case, EvidenceItem, UnverifiedItem
from provcheck.core.db.session import session_scope
from provcheck.core.interfaces import (
    C2PAReading,
    ELAReading,
    ExifReading,
    NotConfigured,
    ProviderError,
    ProviderUnavailable,
)
from provcheck.core.investigators.text_helpers import extract_image_urls
from provcheck.core.pipeline import orchestrator
from provcheck.core.providers.registry import Providers
from provcheck.core.security.ssrf import BlockedURL

log = logging.getLogger(__name__)
INVESTIGATOR = "media"


# ---------- Per-item analysis ------------------------------------------------


async def _analyse_item(
    *,
    data: bytes,
    source_url: str | None,
    sha256: str | None,
    claimed_published_at: datetime | None,
    providers: Providers,
) -> tuple[list[EvidenceItem], list[UnverifiedItem], bytes | None, str | None]:
    """Return (evidence, unverified, ela_diff_png, ela_signal_strength)."""
    ev: list[EvidenceItem] = []
    un: list[UnverifiedItem] = []

    # --- EXIF ----------------------------------------------------------------
    exif_reading: ExifReading | None = None
    try:
        exif_reading = await providers.exif.read(data)
    except ProviderError as e:
        un.append(_unv(source_url or sha256 or "image", "error", f"exif: {e}"))

    if exif_reading is not None and exif_reading.has_exif:
        if exif_reading.datetime_original is not None and claimed_published_at is not None:
            delta_days = abs(
                (claimed_published_at.replace(tzinfo=None) - exif_reading.datetime_original).days
            )
            if delta_days > 365:
                ev.append(
                    EvidenceItem(
                        id=uuid.uuid4(),
                        case_id=uuid.UUID(int=0),  # overwritten by caller
                        run_id=uuid.UUID(int=0),  # overwritten by caller
                        investigator=INVESTIGATOR,
                        signal="media.exif_date_mismatch",
                        finding=(
                            f"EXIF date {exif_reading.datetime_original.date().isoformat()} "
                            f"differs from article date "
                            f"{claimed_published_at.date().isoformat()} by {delta_days} days."
                        ),
                        source_url=source_url,
                        source_published_at=exif_reading.datetime_original,
                        direction="undermines",
                        strength=0.6,
                        confidence=0.7,
                        category_hint="misleading_context",
                        details={
                            "exif_date": exif_reading.datetime_original.isoformat(),
                            "article_date": claimed_published_at.isoformat(),
                            "delta_days": delta_days,
                            "camera": f"{exif_reading.camera_make or ''} "
                            f"{exif_reading.camera_model or ''}".strip(),
                        },
                        source_content_sha256=sha256,
                    )
                )
    elif exif_reading is not None:
        un.append(_unv(source_url or sha256 or "image", "no_results", "no EXIF metadata present"))

    # --- C2PA ----------------------------------------------------------------
    c2pa_reading: C2PAReading | None = None
    try:
        c2pa_reading = await providers.c2pa.verify(data)
    except NotConfigured as e:
        un.append(_unv(source_url or sha256 or "image", "not_configured", f"c2pa: {e}"))
    except (ProviderUnavailable, ProviderError) as e:
        un.append(
            _unv(
                source_url or sha256 or "image",
                "provider_unavailable",
                f"c2pa: {type(e).__name__}: {e}",
            )
        )
    if c2pa_reading is not None:
        if c2pa_reading.state == "valid":
            ev.append(
                EvidenceItem(
                    id=uuid.uuid4(),
                    case_id=uuid.UUID(int=0),
                    run_id=uuid.UUID(int=0),
                    investigator=INVESTIGATOR,
                    signal="media.c2pa_valid",
                    finding=f"C2PA manifest is valid ({len(c2pa_reading.manifests)} record(s)).",
                    source_url=source_url,
                    direction="supports",
                    strength=0.8,
                    confidence=0.9,
                    category_hint="corroborated",
                    details={"manifests": c2pa_reading.manifests},
                    source_content_sha256=sha256,
                )
            )
        elif c2pa_reading.state == "invalid":
            ev.append(
                EvidenceItem(
                    id=uuid.uuid4(),
                    case_id=uuid.UUID(int=0),
                    run_id=uuid.UUID(int=0),
                    investigator=INVESTIGATOR,
                    signal="media.c2pa_invalid",
                    finding="C2PA manifest present but failed validation.",
                    source_url=source_url,
                    direction="undermines",
                    strength=0.8,
                    confidence=0.9,
                    category_hint="fabricated",
                    details={"error": c2pa_reading.error, "manifests": c2pa_reading.manifests},
                    source_content_sha256=sha256,
                )
            )
        elif c2pa_reading.state == "missing":
            un.append(_unv(source_url or sha256 or "image", "no_results", "no C2PA manifest found"))
        else:
            un.append(
                _unv(
                    source_url or sha256 or "image",
                    "no_results",
                    f"c2pa result unverified: {c2pa_reading.error or 'unknown'}",
                )
            )

    # --- ELA (informational, weight 0) ---------------------------------------
    ela_bytes: bytes | None = None
    ela_mean: float | None = None
    is_jpeg = exif_reading is not None and exif_reading.mime == "image/jpeg"
    if is_jpeg:
        try:
            ela: ELAReading = await providers.ela.analyze(data)
            ela_bytes = ela.diff_png
            ela_mean = ela.mean_abs_difference
            ev.append(
                EvidenceItem(
                    id=uuid.uuid4(),
                    case_id=uuid.UUID(int=0),
                    run_id=uuid.UUID(int=0),
                    investigator=INVESTIGATOR,
                    signal="media.ela",
                    finding=(
                        f"Error-Level Analysis diff computed (mean={ela.mean_abs_difference:.1f}, "
                        f"max={ela.max_abs_difference})."
                    ),
                    source_url=source_url,
                    direction="neutral",
                    strength=0.0,
                    confidence=0.5,
                    category_hint=None,
                    details={
                        "mean_abs_difference": ela.mean_abs_difference,
                        "max_abs_difference": ela.max_abs_difference,
                        "informational": True,
                    },
                    source_content_sha256=sha256,
                )
            )
        except ProviderError as e:
            un.append(_unv(source_url or sha256 or "image", "error", f"ela: {e}"))

    # --- Detector ------------------------------------------------------------
    try:
        await providers.detector.classify(data)
    except NotConfigured as e:
        un.append(_unv(source_url or sha256 or "image", "not_configured", f"detector: {e}"))

    _ = ela_mean  # kept for future tuning
    return ev, un, ela_bytes, None


# ---------- Entry point ------------------------------------------------------


def _unv(subject: str, reason: str, detail: str) -> UnverifiedItem:
    return UnverifiedItem(
        id=uuid.uuid4(),
        case_id=uuid.UUID(int=0),  # overwritten by caller
        run_id=uuid.UUID(int=0),
        investigator=INVESTIGATOR,
        subject=subject,
        reason=reason,
        detail=detail,
    )


def _rebind(items: list[Any], case_id: uuid.UUID, run_id: uuid.UUID) -> list[Any]:
    for i in items:
        i.case_id = case_id
        i.run_id = run_id
    return items


async def _gather_url_media(
    *,
    html: str,
    base_url: str | None,
    providers: Providers,
    max_items: int,
) -> list[tuple[str, bytes, str]]:
    """Download up to ``max_items`` image URLs. Returns [(url, bytes, sha256)]."""
    import hashlib

    urls = extract_image_urls(html, base_url=base_url)[:max_items]
    out: list[tuple[str, bytes, str]] = []
    fetcher = providers.fetcher
    for url in urls:
        try:
            result = await fetcher.fetch(url, allowed_content_types=("image/",))
        except BlockedURL as e:
            log.info("media: blocked image URL %s: %s", url, e.reason)
            continue
        except Exception as e:
            log.info("media: failed to fetch %s: %s", url, type(e).__name__)
            continue
        if result.status_code != 200 or not result.content:
            continue
        out.append((url, result.content, hashlib.sha256(result.content).hexdigest()))
    return out


async def _run_async(
    *,
    case_id: uuid.UUID,
    run_id: uuid.UUID,
    items: list[tuple[str | None, bytes, str | None]],
    claimed_published_at: datetime | None,
    providers: Providers,
) -> tuple[list[EvidenceItem], list[UnverifiedItem], list[bytes]]:
    all_ev: list[EvidenceItem] = []
    all_un: list[UnverifiedItem] = []
    ela_blobs: list[bytes] = []
    for source_url, data, sha256 in items:
        ev, un, ela_bytes, _ = await _analyse_item(
            data=data,
            source_url=source_url,
            sha256=sha256,
            claimed_published_at=claimed_published_at,
            providers=providers,
        )
        all_ev.extend(_rebind(ev, case_id, run_id))
        all_un.extend(_rebind(un, case_id, run_id))
        if ela_bytes is not None:
            ela_blobs.append(ela_bytes)
    return all_ev, all_un, ela_blobs


def _load_html(capture: Capture, providers: Providers) -> str:
    if capture.html_artifact_id is None:
        return ""
    with session_scope() as s:
        art = s.get(Artifact, capture.html_artifact_id)
        key = art.object_key if art else None
    if not key:
        return ""
    try:
        data = asyncio.run(providers.object_store.get(key))
        return data.decode("utf-8", errors="replace")
    except Exception as e:
        log.info("media: failed to load html artifact: %s", e)
        return ""


def _load_upload(case: Case, providers: Providers) -> tuple[str | None, bytes, str] | None:
    """Return (source_url, bytes, sha256) for the uploaded artifact, or None."""
    if case.upload_artifact_id is None:
        return None
    with session_scope() as s:
        art = s.get(Artifact, case.upload_artifact_id)
        if art is None:
            return None
        key = art.object_key
        sha256 = art.sha256
    try:
        data = asyncio.run(providers.object_store.get(key))
    except Exception as e:
        log.info("media: failed to load upload artifact: %s", e)
        return None
    return None, data, sha256


def run_media(case_id: uuid.UUID, run_id: uuid.UUID, providers: Providers) -> None:
    run = orchestrator.start_run(case_id, run_id)
    if run is None:
        return

    with session_scope() as s:
        case = s.get(Case, case_id)
        if case is None:
            orchestrator.finish_run(case_id, run_id, status="skipped", counts={"reason": 0})
            return
        input_type = case.input_type
        case_snapshot = {"id": case.id, "upload_artifact_id": case.upload_artifact_id}
        cap = (
            s.query(Capture)
            .filter(Capture.case_id == case_id)
            .order_by(Capture.fetched_at.desc())
            .first()
        )
        if cap is not None:
            s.expunge(cap)
    _ = case_snapshot

    try:
        if input_type == "text":
            orchestrator.finish_run(
                case_id,
                run_id,
                status="skipped",
                counts={"reason": 0},
                providers_used={"detector": providers.detector.name},
            )
            return

        items: list[tuple[str | None, bytes, str | None]] = []
        claimed_dt: datetime | None = None

        if input_type == "upload":
            u = _load_upload(_fetch_case(case_id), providers)
            if u is not None:
                items.append(u)
        else:
            if cap is None:
                orchestrator.finish_run(
                    case_id,
                    run_id,
                    status="skipped",
                    counts={"reason": 0},
                )
                return
            html = _load_html(cap, providers)
            downloaded = asyncio.run(
                _gather_url_media(
                    html=html,
                    base_url=cap.final_url,
                    providers=providers,
                    max_items=providers.settings.media_max_images_per_case,
                )
            )
            items = [(url, data, sha256) for url, data, sha256 in downloaded]
            if cap.claimed_published_at:
                try:
                    claimed_dt = datetime.fromisoformat(
                        cap.claimed_published_at.replace("Z", "+00:00")
                    )
                except ValueError:
                    claimed_dt = None

        if not items:
            orchestrator.finish_run(
                case_id,
                run_id,
                status="skipped",
                counts={"items": 0},
                providers_used={
                    "exif": providers.exif.name,
                    "c2pa": providers.c2pa.name,
                    "ela": providers.ela.name,
                    "detector": providers.detector.name,
                },
            )
            return

        ev, un, ela_blobs = asyncio.run(
            _run_async(
                case_id=case_id,
                run_id=run_id,
                items=items,
                claimed_published_at=claimed_dt,
                providers=providers,
            )
        )
    except Exception as e:
        log.exception("media crashed: case=%s", case_id)
        orchestrator.finish_run(case_id, run_id, status="failed", error=f"{type(e).__name__}: {e}")
        return

    # Persist ELA diffs as artifacts for the report.
    stored_ela: list[Artifact] = []
    for ela_bytes in ela_blobs:
        try:
            obj = asyncio.run(
                providers.object_store.put(ela_bytes, content_type="image/png", prefix="ela")
            )
            stored_ela.append(
                Artifact(
                    id=uuid.uuid4(),
                    case_id=case_id,
                    kind="ela",
                    object_key=obj.key,
                    sha256=obj.sha256,
                    size=obj.size,
                    mime=obj.content_type,
                )
            )
        except Exception as e:
            log.info("media: failed to store ELA artifact: %s", e)

    with session_scope() as s:
        for e_item in ev:
            s.add(e_item)
        for u_item in un:
            s.add(u_item)
        for art in stored_ela:
            s.add(art)
        audit.append(
            s,
            "media.wrote",
            {
                "evidence": len(ev),
                "unverified": len(un),
                "ela_artifacts": len(stored_ela),
                "items_analysed": len(items),
            },
            case_id=case_id,
        )

    orchestrator.finish_run(
        case_id,
        run_id,
        status="done",
        providers_used={
            "exif": providers.exif.name,
            "c2pa": providers.c2pa.name,
            "ela": providers.ela.name,
            "detector": providers.detector.name,
        },
        counts={"evidence": len(ev), "unverified": len(un)},
    )


def _fetch_case(case_id: uuid.UUID) -> Case:
    with session_scope() as s:
        c = s.get(Case, case_id)
        if c is None:
            raise ValueError(f"case {case_id} vanished")
        s.expunge(c)
        return c
