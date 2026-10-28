"""FastAPI application: submissions, case status, artifacts, audit."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from provcheck import logging_setup
from provcheck.config import get_settings
from provcheck.core.db import audit
from provcheck.core.db.models import Artifact, AuditLog, Capture, Case, Report
from provcheck.core.db.session import get_sessionmaker
from provcheck.core.models.case import (
    ArtifactOut,
    AuditEntryOut,
    AuditOut,
    CaptureOut,
    CaseCreate,
    CaseOut,
)
from provcheck.core.pipeline import state
from provcheck.core.providers.registry import Providers, get_providers
from provcheck.core.report import DISCLAIMER
from provcheck.core.security.ssrf import BlockedURL, URLPolicy, validate_url
from provcheck.core.security.uploads import UploadRejected, validate_upload

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

log = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
}
# Captured pages are attacker-controlled: never render them on our origin.
UNTRUSTED_ARTIFACT_HEADERS = {
    "Content-Security-Policy": "sandbox; default-src 'none'",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, max-age=3600",
}
SAFE_MIME = {
    "screenshot": "image/png",
    "text": "text/plain; charset=utf-8",
    "html": "text/plain; charset=utf-8",
    "media": "application/octet-stream",  # never serve uploads with their claimed type
    "ela": "image/png",
}


def get_session() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def providers_dep() -> Providers:
    return get_providers()


def create_app() -> FastAPI:
    settings = get_settings()
    logging_setup.configure(settings.log_level)
    app = FastAPI(
        title="Provenance Check",
        version="0.1.0",
        description="Credibility assessment with an evidence trail. Not a verdict.",
    )

    @app.middleware("http")
    async def _headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(
        session: Session = Depends(get_session), providers: Providers = Depends(providers_dep)
    ) -> JSONResponse:
        checks: dict[str, dict[str, object]] = {}
        try:
            session.execute(text("SELECT 1"))
            checks["postgres"] = {"ok": True}
        except Exception as e:
            checks["postgres"] = {"ok": False, "detail": type(e).__name__}
        q = providers.queue.health()
        checks["queue"] = {"ok": q.ok, "detail": q.detail}
        o = asyncio.run(providers.object_store.health())
        checks["object_store"] = {"ok": o.ok, "detail": o.detail}
        ok = all(c["ok"] for c in checks.values())
        return JSONResponse({"ok": ok, "checks": checks}, status_code=200 if ok else 503)

    @app.post("/v1/cases", status_code=202, response_model=CaseOut)
    def create_case(
        body: CaseCreate,
        response: Response,
        session: Session = Depends(get_session),
        providers: Providers = Depends(providers_dep),
    ) -> CaseOut:
        if body.text is not None:
            return _case_out(session, _create_text_case(session, providers, body))
        assert body.url is not None  # model_validator guarantees exactly one input
        case, cached = _create_url_case_or_hit_cache(session, providers, settings, body)
        if cached:
            response.status_code = 200
            out = _case_out(session, case)
            out.cached = True
            return out
        return _case_out(session, case)

    @app.post("/v1/cases/upload", status_code=202, response_model=CaseOut)
    async def create_case_upload(
        file: UploadFile = File(...),
        session: Session = Depends(get_session),
        providers: Providers = Depends(providers_dep),
    ) -> CaseOut:
        case = await _create_upload_case(session, providers, settings, file)
        return _case_out(session, case)

    @app.get("/v1/cases/{case_id}", response_model=CaseOut)
    def get_case(case_id: uuid.UUID, session: Session = Depends(get_session)) -> CaseOut:
        case = session.get(Case, case_id)
        if case is None:
            raise HTTPException(404, "case not found")
        return _case_out(session, case)

    @app.get("/v1/cases/{case_id}/artifacts/{artifact_id}")
    def get_artifact(
        case_id: uuid.UUID,
        artifact_id: uuid.UUID,
        session: Session = Depends(get_session),
        providers: Providers = Depends(providers_dep),
    ) -> Response:
        art = session.get(Artifact, artifact_id)
        if art is None or art.case_id != case_id:
            raise HTTPException(404, "artifact not found")
        data = asyncio.run(providers.object_store.get(art.object_key))
        headers = dict(UNTRUSTED_ARTIFACT_HEADERS)
        headers["X-Content-SHA256"] = art.sha256
        if art.kind == "html":
            headers["Content-Disposition"] = f'attachment; filename="{art.sha256[:16]}.html.txt"'
        return Response(
            content=data,
            media_type=SAFE_MIME.get(art.kind, "application/octet-stream"),
            headers=headers,
        )

    @app.get("/v1/cases/{case_id}/report.json")
    def get_report_json(
        case_id: uuid.UUID,
        session: Session = Depends(get_session),
        providers: Providers = Depends(providers_dep),
    ) -> Response:
        report_row = _latest_report(session, case_id)
        if report_row is None:
            raise HTTPException(404, "no report (case may still be scoring, or has failed)")
        art = session.get(Artifact, report_row.artifact_id)
        if art is None:
            raise HTTPException(500, "report artifact missing")
        data = asyncio.run(providers.object_store.get(art.object_key))
        return Response(
            content=data,
            media_type="application/json",
            headers={"X-Content-Type-Options": "nosniff", "X-Content-SHA256": art.sha256},
        )

    @app.get("/v1/cases/{case_id}/report", response_class=HTMLResponse)
    def get_report_html(
        case_id: uuid.UUID,
        request: Request,
        session: Session = Depends(get_session),
        providers: Providers = Depends(providers_dep),
    ) -> HTMLResponse:
        case = session.get(Case, case_id)
        if case is None:
            raise HTTPException(404, "case not found")
        report_doc = _load_report_doc(session, providers, case_id)
        return templates.TemplateResponse(
            request, "_case_fragment.html", {"case": case, "report": report_doc}
        )

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html", {"disclaimer": DISCLAIMER})

    @app.get("/cases/{case_id}", response_class=HTMLResponse)
    def ui_case(
        case_id: uuid.UUID,
        request: Request,
        session: Session = Depends(get_session),
        providers: Providers = Depends(providers_dep),
    ) -> HTMLResponse:
        case = session.get(Case, case_id)
        if case is None:
            raise HTTPException(404, "case not found")
        report_doc = (
            _load_report_doc(session, providers, case_id)
            if case.status in ("complete", "failed")
            else None
        )
        return templates.TemplateResponse(
            request, "case.html", {"case": case, "report": report_doc}
        )

    @app.get("/ui/cases/{case_id}/fragment", response_class=HTMLResponse)
    def ui_case_fragment(
        case_id: uuid.UUID,
        request: Request,
        session: Session = Depends(get_session),
        providers: Providers = Depends(providers_dep),
    ) -> HTMLResponse:
        case = session.get(Case, case_id)
        if case is None:
            raise HTTPException(404, "case not found")
        report_doc = (
            _load_report_doc(session, providers, case_id)
            if case.status in ("complete", "failed")
            else None
        )
        return templates.TemplateResponse(
            request, "_case_fragment.html", {"case": case, "report": report_doc}
        )

    @app.post("/ui/submit-url")
    def ui_submit_url(
        url: str = Form(...),
        archive: bool = Form(False),
        session: Session = Depends(get_session),
        providers: Providers = Depends(providers_dep),
    ) -> RedirectResponse:
        body = CaseCreate(url=url, archive=archive)
        case = _create_url_case(session, providers, settings, body)
        return RedirectResponse(url=f"/cases/{case.id}", status_code=303)

    @app.post("/ui/submit-text")
    def ui_submit_text(
        text_body: str = Form(..., alias="text"),
        session: Session = Depends(get_session),
        providers: Providers = Depends(providers_dep),
    ) -> RedirectResponse:
        body = CaseCreate(text=text_body)
        case = _create_text_case(session, providers, body)
        return RedirectResponse(url=f"/cases/{case.id}", status_code=303)

    @app.post("/ui/submit-upload")
    async def ui_submit_upload(
        file: UploadFile = File(...),
        session: Session = Depends(get_session),
        providers: Providers = Depends(providers_dep),
    ) -> RedirectResponse:
        case = await _create_upload_case(session, providers, settings, file)
        return RedirectResponse(url=f"/cases/{case.id}", status_code=303)

    @app.get("/v1/cases/{case_id}/audit", response_model=AuditOut)
    def get_audit(case_id: uuid.UUID, session: Session = Depends(get_session)) -> AuditOut:
        if session.get(Case, case_id) is None:
            raise HTTPException(404, "case not found")
        entries = list(
            session.execute(
                select(AuditLog).where(AuditLog.case_id == case_id).order_by(AuditLog.id)
            ).scalars()
        )
        own = audit.verify_entries(entries, check_links=False)
        chain = audit.verify_chain(session)
        return AuditOut(
            case_id=case_id,
            entries_valid=own.ok,
            chain_valid=chain.ok,
            chain_detail=chain.reason or f"{chain.checked} entries verified",
            entries=[
                AuditEntryOut(
                    id=e.id,
                    ts=e.ts,
                    actor=e.actor,
                    event_type=e.event_type,
                    payload=e.payload,
                    prev_hash=e.prev_hash,
                    hash=e.hash,
                )
                for e in entries
            ],
        )

    return app


def _case_out(session: Session, case: Case) -> CaseOut:
    captures = list(
        session.execute(
            select(Capture).where(Capture.case_id == case.id).order_by(Capture.fetched_at)
        ).scalars()
    )
    out_caps: list[CaptureOut] = []
    for c in captures:
        ids = [i for i in (c.html_artifact_id, c.screenshot_artifact_id, c.text_artifact_id) if i]
        arts = (
            list(session.execute(select(Artifact).where(Artifact.id.in_(ids))).scalars())
            if ids
            else []
        )
        out_caps.append(
            CaptureOut(
                requested_url=c.requested_url,
                final_url=c.final_url,
                redirect_chain=c.redirect_chain,
                http_status=c.http_status,
                fetched_at=c.fetched_at,
                title=c.title,
                claimed_published_at=c.claimed_published_at,
                text_sha256=c.text_sha256,
                truncated=c.truncated,
                blocked_requests=len(c.blocked_requests or []),
                archive={
                    "status": c.archive_status,
                    "url": c.archive_url,
                    "error": c.archive_error,
                },
                artifacts=[
                    ArtifactOut(
                        id=a.id,
                        kind=a.kind,
                        sha256=a.sha256,
                        size=a.size,
                        mime=a.mime,
                        href=f"/v1/cases/{case.id}/artifacts/{a.id}",
                    )
                    for a in sorted(arts, key=lambda a: a.kind)
                ],
            )
        )
    return CaseOut(
        id=case.id,
        status=case.status,
        input_type=case.input_type,
        input_url=case.input_url,
        normalized_url=case.normalized_url,
        created_at=case.created_at,
        updated_at=case.updated_at,
        error=case.error,
        captures=out_caps,
    )


# -------------- Case-creation helpers (shared by JSON API + UI) ---------------


def _create_text_case(session: Session, providers: Providers, body: CaseCreate) -> Case:
    assert body.text is not None
    case = Case(
        id=uuid.uuid4(),
        input_type="text",
        input_text=body.text,
        cache_key=hashlib.sha256(body.text.encode()).hexdigest(),
        status="extracting",
        archive_opt_out=True,
    )
    session.add(case)
    session.flush()
    audit.append(
        session,
        "case.created",
        {"input_type": "text", "chars": len(body.text)},
        case_id=case.id,
    )
    session.commit()
    state.enqueue_stage(providers.queue, case.id, "extracting")
    session.refresh(case)
    return case


def _create_url_case_or_hit_cache(
    session: Session,
    providers: Providers,
    settings_obj: object,
    body: CaseCreate,
) -> tuple[Case, bool]:
    """Returns (case, cached_hit)."""
    assert body.url is not None
    policy = URLPolicy.from_settings(settings_obj)
    try:
        target = asyncio.run(validate_url(body.url, policy))
    except BlockedURL as e:
        raise HTTPException(422, detail=f"URL not allowed: {e.reason}") from e
    cache_key = hashlib.sha256(target.url.encode()).hexdigest()

    if not body.refresh:
        ttl = getattr(settings_obj, "result_cache_ttl_s", 0)
        cutoff = datetime.now(UTC) - timedelta(seconds=ttl)
        cached = session.execute(
            select(Case)
            .where(
                Case.cache_key == cache_key,
                Case.status == "complete",
                Case.created_at >= cutoff,
            )
            .order_by(Case.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if cached is not None:
            return cached, True

    case = Case(
        id=uuid.uuid4(),
        input_type="url",
        input_url=body.url,
        normalized_url=target.url,
        cache_key=cache_key,
        status="queued",
        archive_opt_out=not body.archive,
    )
    session.add(case)
    session.flush()
    audit.append(
        session,
        "case.created",
        {
            "input_type": "url",
            "url": body.url,
            "normalized_url": target.url,
            "archive": body.archive,
        },
        case_id=case.id,
    )
    session.commit()
    state.enqueue_stage(providers.queue, case.id, "capturing")
    session.refresh(case)
    return case, False


def _create_url_case(
    session: Session, providers: Providers, settings_obj: object, body: CaseCreate
) -> Case:
    """UI wrapper: ignore the cache hit (always refresh) so the user never lands
    on a stale case id. The /v1 JSON endpoint still honors the cache."""
    body = body.model_copy(update={"refresh": True})
    case, _ = _create_url_case_or_hit_cache(session, providers, settings_obj, body)
    return case


async def _create_upload_case(
    session: Session, providers: Providers, settings_obj: object, file: UploadFile
) -> Case:
    max_bytes: int = getattr(settings_obj, "upload_max_bytes", 25 * 1024 * 1024)
    allowed: list[str] = getattr(settings_obj, "upload_allowed_mimes", [])
    raw = await file.read(max_bytes + 1)
    try:
        v = validate_upload(raw, max_bytes=max_bytes, allowed_mimes=allowed)
    except UploadRejected as e:
        raise HTTPException(422, detail=f"upload rejected: {e}") from e

    obj = await providers.object_store.put(v.data, content_type=v.detected_mime, prefix="uploads")
    from provcheck.core.providers.local_phash import compute_phash

    try:
        phash = compute_phash(v.data) if v.detected_mime.startswith("image/") else None
    except Exception:
        phash = None

    case = Case(
        id=uuid.uuid4(),
        input_type="upload",
        cache_key=hashlib.sha256(obj.sha256.encode()).hexdigest(),
        status="investigating",
        archive_opt_out=True,
    )
    session.add(case)
    session.flush()
    artifact = Artifact(
        id=uuid.uuid4(),
        case_id=case.id,
        kind="media",
        object_key=obj.key,
        sha256=obj.sha256,
        size=obj.size,
        mime=v.detected_mime,
        phash=phash,
    )
    session.add(artifact)
    session.flush()
    case.upload_artifact_id = artifact.id
    audit.append(
        session,
        "case.created",
        {
            "input_type": "upload",
            "detected_mime": v.detected_mime,
            "size": v.size,
            "sha256": obj.sha256,
        },
        case_id=case.id,
    )
    session.commit()
    state.enqueue_stage(providers.queue, case.id, "investigating")
    session.refresh(case)
    return case


def _latest_report(session: Session, case_id: uuid.UUID) -> Report | None:
    return session.execute(
        select(Report).where(Report.case_id == case_id).order_by(Report.created_at.desc()).limit(1)
    ).scalar_one_or_none()


def _load_report_doc(
    session: Session, providers: Providers, case_id: uuid.UUID
) -> dict[str, object] | None:
    row = _latest_report(session, case_id)
    if row is None:
        return None
    art = session.get(Artifact, row.artifact_id)
    if art is None:
        return None
    data = asyncio.run(providers.object_store.get(art.object_key))
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


app = create_app()
