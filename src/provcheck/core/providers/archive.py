"""Internet Archive Wayback Machine provider.

Save Page Now 2 (SPN2), checked against the public SPN2 API doc (Google Doc
"Save Page Now 2 Public API Docs", last read 2026-10-26):
  * POST https://web.archive.org/save  form: url=...   header: Authorization: LOW key:secret,
    Accept: application/json  -> {"url", "job_id"}
  * GET  https://web.archive.org/save/status/<job_id> -> status pending|success|error;
    success has timestamp + original_url; error has status_ext + message.
  * Capture URL pattern: https://web.archive.org/web/<timestamp>/<original_url>
  * Limits: 7 captures/min authenticated; status results kept ~1 hour.
"""

from __future__ import annotations

from typing import Any

import httpx

from provcheck.core.interfaces import (
    ArchiveCapture,
    ArchiveJob,
    ArchiveJobStatus,
    NotConfigured,
    ProviderError,
    ProviderHealth,
    ProviderUnavailable,
)
from provcheck.core.security.ssrf import trusted_client


class WaybackArchive:
    name = "wayback"

    def __init__(self, *, base_url: str, access_key: str, secret_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._auth = f"LOW {access_key}:{secret_key}" if access_key and secret_key else None

    def _headers(self) -> dict[str, str]:
        if self._auth is None:
            raise NotConfigured("Internet Archive S3 keys not configured (SPN2 requires auth)")
        return {"Authorization": self._auth, "Accept": "application/json"}

    async def _request(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        async with trusted_client(self.base_url, timeout=60) as client:
            try:
                resp = await client.request(method, path, headers=self._headers(), **kw)
            except httpx.TimeoutException as e:
                raise ProviderUnavailable("wayback timeout") from e
            except httpx.HTTPError as e:
                raise ProviderUnavailable(f"wayback unreachable: {type(e).__name__}") from e
        if resp.status_code == 429 or resp.status_code >= 500:
            raise ProviderUnavailable(f"wayback HTTP {resp.status_code}")
        if resp.status_code in (401, 403):
            raise NotConfigured(f"wayback rejected credentials (HTTP {resp.status_code})")
        try:
            data = resp.json()
        except ValueError as e:
            raise ProviderError(f"wayback returned non-JSON (HTTP {resp.status_code})") from e
        if not isinstance(data, dict):
            raise ProviderError("wayback returned unexpected JSON")
        return data

    async def save(self, url: str) -> ArchiveJob:
        data = await self._request("POST", "/save", data={"url": url})
        job_id = data.get("job_id")
        if not job_id:
            raise ProviderError(
                f"wayback save refused: {data.get('status_ext') or data.get('message') or data}"
            )
        return ArchiveJob(job_id=str(job_id), url=str(data.get("url") or url))

    async def status(self, job: ArchiveJob) -> ArchiveJobStatus:
        data = await self._request("GET", f"/save/status/{job.job_id}")
        state = data.get("status")
        if state == "success":
            ts = str(data.get("timestamp") or "")
            original = str(data.get("original_url") or job.url)
            return ArchiveJobStatus(
                job_id=job.job_id,
                state="success",
                timestamp=ts,
                archive_url=f"{self.base_url}/web/{ts}/{original}" if ts else None,
            )
        if state == "error":
            return ArchiveJobStatus(
                job_id=job.job_id,
                state="error",
                error_code=str(data.get("status_ext") or "error"),
                message=str(data.get("message") or "")[:500],
            )
        return ArchiveJobStatus(job_id=job.job_id, state="pending")

    async def history(self, url: str, *, limit: int = 50) -> list[ArchiveCapture]:
        raise NotConfigured("archive history (CDX) lookup arrives in build step 3")

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, self._auth is not None, "" if self._auth else "no keys")


class NullArchive:
    name = "none"

    async def save(self, url: str) -> ArchiveJob:
        raise NotConfigured("archive provider disabled")

    async def status(self, job: ArchiveJob) -> ArchiveJobStatus:
        raise NotConfigured("archive provider disabled")

    async def history(self, url: str, *, limit: int = 50) -> list[ArchiveCapture]:
        raise NotConfigured("archive provider disabled")

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "disabled")
