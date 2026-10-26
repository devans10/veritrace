import base64
from datetime import UTC, datetime

import httpx
import pytest
import respx

from provcheck.core.interfaces import ArchiveJob, NotConfigured, ProviderError, ProviderUnavailable
from provcheck.core.providers.archive import NullArchive, WaybackArchive
from provcheck.core.providers.browser import HTTPBrowserCapture
from provcheck.core.providers.objectstore import content_key
from provcheck.core.providers.queue import InlineQueue
from provcheck.core.security.ssrf import BlockedURL

WB = "https://web.archive.org"


def _wb():
    return WaybackArchive(base_url=WB, access_key="AK", secret_key="SK")


@respx.mock
async def test_wayback_save_sends_low_auth_and_returns_job():
    route = respx.post(f"{WB}/save").mock(
        return_value=httpx.Response(200, json={"url": "https://ex.com/", "job_id": "spn2-abc"})
    )
    job = await _wb().save("https://ex.com/")
    assert job.job_id == "spn2-abc"
    req = route.calls.last.request
    assert req.headers["authorization"] == "LOW AK:SK"
    assert req.headers["accept"] == "application/json"
    assert b"url=https" in req.content


@respx.mock
async def test_wayback_status_success_builds_archive_url():
    respx.get(f"{WB}/save/status/spn2-abc").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "job_id": "spn2-abc",
                "timestamp": "20261026120000",
                "original_url": "https://ex.com/a",
                "duration_sec": 4.2,
            },
        )
    )
    st = await _wb().status(ArchiveJob("spn2-abc", "https://ex.com/a"))
    assert st.state == "success"
    assert st.archive_url == f"{WB}/web/20261026120000/https://ex.com/a"


@respx.mock
async def test_wayback_status_error_and_pending():
    respx.get(f"{WB}/save/status/e1").mock(
        return_value=httpx.Response(
            200, json={"status": "error", "status_ext": "error:blocked-url", "message": "blocked"}
        )
    )
    respx.get(f"{WB}/save/status/p1").mock(
        return_value=httpx.Response(
            200, json={"status": "pending", "job_id": "p1", "resources": []}
        )
    )
    wb = _wb()
    e = await wb.status(ArchiveJob("e1", "u"))
    assert (e.state, e.error_code) == ("error", "error:blocked-url")
    assert (await wb.status(ArchiveJob("p1", "u"))).state == "pending"


@respx.mock
async def test_wayback_rate_limit_is_unavailable_and_no_job_is_error():
    respx.post(f"{WB}/save").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json={"status": "error", "status_ext": "x"}),
        ]
    )
    with pytest.raises(ProviderUnavailable):
        await _wb().save("https://ex.com/")
    with pytest.raises(ProviderError):
        await _wb().save("https://ex.com/")


async def test_wayback_without_keys_is_not_configured():
    with pytest.raises(NotConfigured):
        await WaybackArchive(base_url=WB, access_key="", secret_key="").save("https://ex.com/")
    with pytest.raises(NotConfigured):
        await NullArchive().save("https://ex.com/")


@respx.mock
async def test_browser_client_maps_responses():
    b = HTTPBrowserCapture("http://browser:3000")
    respx.post("http://browser:3000/capture").mock(
        side_effect=[
            httpx.Response(422, json={"detail": "blocked: localhost"}),
            httpx.Response(504, json={"detail": "navigation timed out"}),
            httpx.Response(
                200,
                json={
                    "requested_url": "http://a.example/",
                    "final_url": "http://a.example/",
                    "status_code": 200,
                    "headers": {},
                    "redirect_chain": [],
                    "title": "t",
                    "html_b64": base64.b64encode(b"<p>x</p>").decode(),
                    "text": "x",
                    "screenshot_b64": base64.b64encode(b"png").decode(),
                    "claimed_published_at": None,
                    "fetched_at": datetime.now(UTC).isoformat(),
                },
            ),
        ]
    )
    with pytest.raises(BlockedURL):
        await b.capture("http://localhost/")
    with pytest.raises(ProviderUnavailable):
        await b.capture("http://a.example/")
    r = await b.capture("http://a.example/")
    assert r.html == b"<p>x</p>" and r.screenshot_png == b"png"


def test_content_key_layout_and_validation():
    h = "ab" + "0" * 62
    assert content_key("captures", h) == f"captures/sha256/ab/{h}"
    with pytest.raises(ValueError):
        content_key("../x", h)


def test_inline_queue_runs_and_records_delayed():
    ran = []
    q = InlineQueue(runner=lambda t, p: ran.append((t, p)))
    ref = q.enqueue("pipeline.capture", {"case_id": "1"}, queue="capture", job_id="j1")
    q.enqueue("pipeline.archive_poll", {"x": 1}, queue="capture", delay_s=30)
    assert ran == [("pipeline.capture", {"case_id": "1"})]
    assert q.status(ref.id) == "finished" and len(q.delayed) == 1


def test_task_registry_rejects_unknown_tasks():
    from provcheck.services.worker.tasks import run_task

    with pytest.raises(ValueError, match="unknown task"):
        run_task("os.system", {})
