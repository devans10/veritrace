"""Client for the sandboxed browser capture service (services/browser)."""

from __future__ import annotations

import base64
from dataclasses import asdict
from datetime import datetime

import httpx

from provcheck.core.interfaces import (
    CaptureOptions,
    CaptureResult,
    ProviderError,
    ProviderHealth,
    ProviderUnavailable,
)
from provcheck.core.security.ssrf import BlockedURL, trusted_client

MAX_RESPONSE_BYTES = 40 * 1024 * 1024


class HTTPBrowserCapture:
    name = "playwright-http"

    def __init__(self, base_url: str, timeout_s: float = 60.0) -> None:
        self.base_url = base_url
        self.timeout_s = timeout_s

    async def capture(self, url: str, opts: CaptureOptions | None = None) -> CaptureResult:
        opts = opts or CaptureOptions()
        async with trusted_client(self.base_url, timeout=self.timeout_s + 15) as client:
            try:
                resp = await client.post("/capture", json={"url": url, **asdict(opts)})
            except httpx.TimeoutException as e:
                raise ProviderUnavailable("browser capture timed out") from e
            except httpx.HTTPError as e:
                raise ProviderUnavailable(f"browser unreachable: {type(e).__name__}") from e
        if len(resp.content) > MAX_RESPONSE_BYTES:
            raise ProviderError("browser response too large")
        if resp.status_code == 422:
            raise BlockedURL(_detail(resp), url)
        if resp.status_code in (502, 503, 504):
            raise ProviderUnavailable(f"browser: {_detail(resp)}")
        if resp.status_code != 200:
            raise ProviderError(f"browser returned {resp.status_code}: {_detail(resp)}")
        d = resp.json()
        return CaptureResult(
            requested_url=d["requested_url"],
            final_url=d["final_url"],
            status_code=d.get("status_code"),
            headers=d.get("headers") or {},
            redirect_chain=d.get("redirect_chain") or [],
            title=d.get("title"),
            html=base64.b64decode(d["html_b64"]),
            text=d.get("text") or "",
            screenshot_png=base64.b64decode(d["screenshot_b64"]),
            claimed_published_at=d.get("claimed_published_at"),
            fetched_at=datetime.fromisoformat(d["fetched_at"]),
            blocked_requests=d.get("blocked_requests") or [],
            truncated=bool(d.get("truncated")),
        )

    async def health(self) -> ProviderHealth:
        try:
            async with trusted_client(self.base_url, timeout=5) as client:
                resp = await client.get("/healthz")
            return ProviderHealth(self.name, resp.status_code == 200)
        except httpx.HTTPError as e:
            return ProviderHealth(self.name, False, type(e).__name__)


def _detail(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("detail", ""))[:500]
    except ValueError:
        return resp.text[:200]
