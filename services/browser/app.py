"""Sandboxed capture service.

Runs in its own container on an internal-only network whose sole route out is the
egress proxy (which blocks private/link-local/loopback destinations). Everything a
page sends or contains is untrusted: we only return bytes and text, never act on them.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from playwright.async_api import Browser, Request, Response, Route, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger("browser")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

PROXY = os.environ.get("EGRESS_PROXY_URL", "http://egress-proxy:4750")
MAX_CONCURRENCY = int(os.environ.get("BROWSER_MAX_CONCURRENCY", "2"))
MAX_HTML_BYTES = int(os.environ.get("BROWSER_MAX_HTML_BYTES", str(5 * 1024 * 1024)))
MAX_TEXT_CHARS = int(os.environ.get("BROWSER_MAX_TEXT_CHARS", str(1_000_000)))
MAX_REQUESTS = int(os.environ.get("BROWSER_MAX_REQUESTS", "400"))
USER_AGENT = os.environ.get(
    "BROWSER_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36 ProvenanceCheck/0.1",
)
DROP_HEADERS = {"set-cookie", "cookie", "authorization"}
# Smokescreen marks its own denial responses (HTTP 407) with this header.
PROXY_ERROR_HEADER = "x-smokescreen-error"

PUBLISHED_AT_JS = r"""
() => {
  const sel = [
    'meta[property="article:published_time"]', 'meta[name="article:published_time"]',
    'meta[itemprop="datePublished"]', 'meta[name="date"]', 'meta[name="pubdate"]',
    'meta[name="DC.date.issued"]', 'time[itemprop="datePublished"]'
  ];
  for (const s of sel) {
    const el = document.querySelector(s);
    if (el) {
      const v = el.getAttribute('content') || el.getAttribute('datetime');
      if (v) return v;
    }
  }
  for (const el of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const stack = [JSON.parse(el.textContent)];
      while (stack.length) {
        const o = stack.pop();
        if (Array.isArray(o)) { stack.push(...o); continue; }
        if (o && typeof o === 'object') {
          if (typeof o.datePublished === 'string') return o.datePublished;
          if (o['@graph']) stack.push(o['@graph']);
        }
      }
    } catch (e) {}
  }
  return null;
}
"""


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=2048)
    timeout_s: float = Field(default=45.0, gt=0, le=90)
    full_page: bool = True
    max_screenshot_height: int = Field(default=12000, ge=500, le=20000)


def _host_blocked(url: str) -> str | None:
    """Cheap in-browser guard. The proxy is the authoritative network control."""
    parts = urlsplit(url)
    if parts.scheme in ("data", "blob", "about"):
        return None
    if parts.scheme not in ("http", "https"):
        return f"scheme {parts.scheme!r}"
    host = (parts.hostname or "").lower()
    if not host or host == "localhost" or host.endswith(".localhost"):
        return "localhost"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    if not ip.is_global:
        return f"non-global IP {ip}"
    return None


state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        # "<-loopback>" removes Chromium's implicit proxy bypass for localhost, so even
        # loopback requests go to the proxy (which refuses them).
        proxy={"server": PROXY, "bypass": "<-loopback>"},
        args=[
            "--disable-dev-shm-usage",
            "--webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--disable-background-networking",
            "--no-first-run",
        ],
    )
    state.update(pw=pw, browser=browser, sem=asyncio.Semaphore(MAX_CONCURRENCY))
    try:
        yield
    finally:
        await browser.close()
        await pw.stop()


app = FastAPI(title="provcheck-browser", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    browser: Browser | None = state.get("browser")
    if browser is None or not browser.is_connected():
        raise HTTPException(503, "browser not running")
    return {"status": "ok", "version": browser.version}


@app.post("/capture")
async def capture(req: CaptureRequest) -> dict[str, Any]:
    reason = _host_blocked(req.url)
    if reason:
        raise HTTPException(422, f"blocked: {reason}")
    async with state["sem"]:
        return await _capture(req)


async def _capture(req: CaptureRequest) -> dict[str, Any]:
    browser: Browser = state["browser"]
    context = await browser.new_context(
        accept_downloads=False,
        service_workers="block",
        user_agent=USER_AGENT,
        viewport={"width": 1280, "height": 900},
        java_script_enabled=True,
        locale="en-US",
    )
    blocked: list[str] = []
    count = 0

    async def guard(route: Route) -> None:
        nonlocal count
        count += 1
        url = route.request.url
        why = _host_blocked(url)
        if why is None and count > MAX_REQUESTS:
            why = "request budget exceeded"
        if why:
            blocked.append(f"{why}: {url[:300]}")
            await route.abort("blockedbyclient")
        else:
            await route.continue_()

    await context.route("**/*", guard)
    page = await context.new_page()

    def on_response(resp: Response) -> None:
        err = resp.headers.get(PROXY_ERROR_HEADER)
        if err:
            blocked.append(f"egress proxy: {err[:200]}: {resp.url[:300]}")

    def on_failed(request: Request) -> None:
        failure = request.failure or ""
        if "ERR_TUNNEL_CONNECTION_FAILED" in failure or "ERR_PROXY" in failure:
            blocked.append(f"egress proxy refused tunnel: {request.url[:300]}")

    page.on("response", on_response)
    page.on("requestfailed", on_failed)
    page.set_default_timeout(req.timeout_s * 1000)
    try:
        try:
            response = await page.goto(req.url, wait_until="load", timeout=req.timeout_s * 1000)
        except PlaywrightTimeout as e:
            raise HTTPException(504, "navigation timed out") from e
        except PlaywrightError as e:
            msg = str(e).splitlines()[0][:300]
            if "ERR_TUNNEL_CONNECTION_FAILED" in msg or "ERR_PROXY" in msg:
                raise HTTPException(502, f"egress proxy refused or could not connect: {msg}") from e
            raise HTTPException(502, f"navigation failed: {msg}") from e
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except PlaywrightTimeout:
            pass

        chain: list[str] = []
        status = None
        headers: dict[str, str] = {}
        if response is not None:
            proxy_err = (await response.all_headers()).get(PROXY_ERROR_HEADER)
            if proxy_err:
                raise HTTPException(422, f"blocked by egress proxy: {proxy_err[:300]}")
            status = response.status
            headers = {
                k: v
                for k, v in (await response.all_headers()).items()
                if k.lower() not in DROP_HEADERS
            }
            r = response.request.redirected_from
            while r is not None:
                chain.insert(0, r.url)
                r = r.redirected_from

        truncated = False
        html = (await page.content()).encode("utf-8")
        if len(html) > MAX_HTML_BYTES:
            html, truncated = html[:MAX_HTML_BYTES], True
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        if len(text) > MAX_TEXT_CHARS:
            text, truncated = text[:MAX_TEXT_CHARS], True
        published = await page.evaluate(PUBLISHED_AT_JS)
        title = await page.title()

        height = await page.evaluate("() => document.documentElement.scrollHeight")
        if req.full_page and height > req.max_screenshot_height:
            shot = await page.screenshot(
                full_page=True,
                clip={"x": 0, "y": 0, "width": 1280, "height": req.max_screenshot_height},
            )
            truncated = True
        else:
            shot = await page.screenshot(full_page=req.full_page)

        return {
            "requested_url": req.url,
            "final_url": page.url,
            "status_code": status,
            "headers": headers,
            "redirect_chain": chain,
            "title": (title or None) and title[:1000],
            "html_b64": base64.b64encode(html).decode(),
            "text": text,
            "screenshot_b64": base64.b64encode(shot).decode(),
            "claimed_published_at": published if isinstance(published, str) else None,
            "fetched_at": datetime.now(UTC).isoformat(),
            "blocked_requests": blocked[:200],
            "truncated": truncated,
        }
    finally:
        await context.close()
