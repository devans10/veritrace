"""Capture stage: render in the sandboxed browser, store snapshot + hashes, request archive."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass

from provcheck.config import Settings
from provcheck.core.db import audit
from provcheck.core.db.models import Artifact, Capture, Case
from provcheck.core.db.session import session_scope
from provcheck.core.interfaces import (
    CaptureResult,
    NotConfigured,
    ProviderError,
    StoredObject,
)
from provcheck.core.pipeline import state
from provcheck.core.providers.registry import Providers
from provcheck.core.security.ssrf import BlockedURL, URLPolicy, parse_target, validate_url

log = logging.getLogger(__name__)
STAGE = "capturing"


def normalized_text_sha256(text: str) -> str:
    """Hash of whitespace-normalised text: stable across trivial re-renders."""
    norm = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


@dataclass
class _Stored:
    result: CaptureResult
    html: StoredObject
    screenshot: StoredObject
    text: StoredObject


async def _capture(url: str, providers: Providers, policy: URLPolicy) -> _Stored:
    await validate_url(url, policy)  # authoritative DNS check before the browser sees it
    result = await providers.browser.capture(url)
    # The browser fetched through the egress proxy, which enforces the block list on
    # its own resolution; re-check the final URL syntactically (scheme, literal IPs).
    parse_target(result.final_url, policy)
    store = providers.object_store
    html = await store.put(result.html, content_type="text/html; charset=utf-8", prefix="captures")
    shot = await store.put(result.screenshot_png, content_type="image/png", prefix="captures")
    text = await store.put(
        result.text.encode("utf-8"), content_type="text/plain; charset=utf-8", prefix="captures"
    )
    return _Stored(result, html, shot, text)


def _artifact(case_id: uuid.UUID, kind: str, obj: StoredObject) -> Artifact:
    return Artifact(
        id=uuid.uuid4(),
        case_id=case_id,
        kind=kind,
        object_key=obj.key,
        sha256=obj.sha256,
        size=obj.size,
        mime=obj.content_type,
    )


def run_capture(case_id: uuid.UUID, providers: Providers) -> None:
    settings: Settings = providers.settings
    policy = URLPolicy.from_settings(settings)

    with session_scope() as s:
        case = s.get(Case, case_id, with_for_update=True)
        if case is None or case.status != "queued":
            log.info("capture skipped: case=%s status=%s", case_id, case and case.status)
            return
        case.status = STAGE
        url = case.input_url or ""
        opted_out = case.archive_opt_out
        audit.append(s, "stage.started", {"stage": "capture", "url": url}, case_id=case_id)

    try:
        stored = asyncio.run(_capture(url, providers, policy))
    except BlockedURL as e:
        with session_scope() as s:
            state.fail_case(s, case_id, STAGE, "blocked_by_policy", e.reason)
        return
    except ProviderError as e:
        with session_scope() as s:
            state.fail_case(s, case_id, STAGE, type(e).__name__, str(e))
        return
    except Exception as e:
        log.exception("capture crashed: case=%s", case_id)
        with session_scope() as s:
            state.fail_case(s, case_id, STAGE, "error", f"{type(e).__name__}: {e}")
        return

    r = stored.result
    with session_scope() as s:
        arts = {
            "html": _artifact(case_id, "html", stored.html),
            "screenshot": _artifact(case_id, "screenshot", stored.screenshot),
            "text": _artifact(case_id, "text", stored.text),
        }
        s.add_all(arts.values())
        s.flush()
        capture = Capture(
            id=uuid.uuid4(),
            case_id=case_id,
            requested_url=r.requested_url,
            final_url=r.final_url,
            redirect_chain=r.redirect_chain,
            http_status=r.status_code,
            response_headers=r.headers,
            fetched_at=r.fetched_at,
            title=r.title,
            claimed_published_at=r.claimed_published_at,
            html_artifact_id=arts["html"].id,
            screenshot_artifact_id=arts["screenshot"].id,
            text_artifact_id=arts["text"].id,
            text_sha256=normalized_text_sha256(r.text),
            blocked_requests=r.blocked_requests[:200],
            truncated=r.truncated,
        )
        s.add(capture)
        s.flush()
        audit.append(
            s,
            "capture.stored",
            {
                "capture_id": str(capture.id),
                "final_url": r.final_url,
                "http_status": r.status_code,
                "redirects": len(r.redirect_chain),
                "blocked_requests": len(r.blocked_requests),
                "truncated": r.truncated,
                "sha256": {k: a.sha256 for k, a in arts.items()},
                "text_sha256": capture.text_sha256,
            },
            case_id=case_id,
        )
        capture_id = capture.id

    request_archive(case_id, capture_id, r.final_url, opted_out, providers)

    with session_scope() as s:
        nxt = state.advance(s, case_id, STAGE)
    if nxt:
        state.enqueue_stage(providers.queue, case_id, nxt)


def request_archive(
    case_id: uuid.UUID, capture_id: uuid.UUID, url: str, opted_out: bool, providers: Providers
) -> None:
    """Submit a Wayback save. Never fails the case; outcome is recorded on the capture."""
    settings = providers.settings
    status, job_id, error = "pending", None, None
    if opted_out:
        status = "opted_out"
    elif not settings.archive_save_enabled or settings.archive_provider == "none":
        status = "disabled"
    else:
        try:
            job = asyncio.run(providers.archive.save(url))
            job_id = job.job_id
        except NotConfigured as e:
            status, error = "not_configured", str(e)
        except ProviderError as e:
            status, error = "failed", f"{type(e).__name__}: {e}"

    with session_scope() as s:
        cap = s.get(Capture, capture_id, with_for_update=True)
        assert cap is not None
        cap.archive_status = status
        cap.archive_job_id = job_id
        cap.archive_error = error
        audit.append(
            s,
            "archive.requested",
            {
                "provider": providers.archive.name,
                "status": status,
                "job_id": job_id,
                "error": error,
            },
            case_id=case_id,
        )
    if status == "pending":
        providers.queue.enqueue(
            "pipeline.archive_poll",
            {"case_id": str(case_id), "capture_id": str(capture_id)},
            queue="capture",
            delay_s=settings.archive_poll_interval_s,
        )


def run_archive_poll(case_id: uuid.UUID, capture_id: uuid.UUID, providers: Providers) -> None:
    from provcheck.core.interfaces import ArchiveJob

    settings = providers.settings
    with session_scope() as s:
        cap = s.get(Capture, capture_id)
        if cap is None or cap.archive_status != "pending" or not cap.archive_job_id:
            return
        job = ArchiveJob(job_id=cap.archive_job_id, url=cap.final_url)
        attempts = cap.archive_attempts + 1

    try:
        st = asyncio.run(providers.archive.status(job))
        new_status, archive_url, error = {
            "success": ("saved", st.archive_url, None),
            "error": ("failed", None, f"{st.error_code}: {st.message}"),
        }.get(st.state, ("pending", None, None))
    except ProviderError as e:
        new_status, archive_url, error = "pending", None, f"{type(e).__name__}: {e}"

    if new_status == "pending" and attempts >= settings.archive_poll_max_attempts:
        new_status, error = "failed", f"timed out after {attempts} status checks ({error or ''})"

    with session_scope() as s:
        cap = s.get(Capture, capture_id, with_for_update=True)
        assert cap is not None
        cap.archive_attempts = attempts
        if new_status != "pending":
            cap.archive_status = new_status
            cap.archive_url = archive_url
            cap.archive_error = error
            audit.append(
                s,
                "archive.finished",
                {
                    "status": new_status,
                    "archive_url": archive_url,
                    "error": error,
                    "attempts": attempts,
                },
                case_id=case_id,
            )
    if new_status == "pending":
        providers.queue.enqueue(
            "pipeline.archive_poll",
            {"case_id": str(case_id), "capture_id": str(capture_id)},
            queue="capture",
            delay_s=settings.archive_poll_interval_s,
        )
