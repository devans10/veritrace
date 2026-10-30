"""SearXNG metasearch adapter.

Hits ``/search?format=json`` on the configured SearXNG instance. JSON format
must be enabled under ``search.formats`` in the SearXNG settings.yml or the
server returns 403.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import httpx

from provcheck.core.interfaces import (
    NotConfigured,
    ProviderError,
    ProviderHealth,
    ProviderUnavailable,
    SearchResult,
    TimeRange,
)
from provcheck.core.security.ssrf import trusted_client

log = logging.getLogger(__name__)


class SearXNGSearch:
    name = "searxng"

    def __init__(self, *, base_url: str) -> None:
        if not base_url:
            raise NotConfigured("SEARXNG_URL is not set")
        self.base_url = base_url.rstrip("/")

    async def search(
        self,
        query: str,
        *,
        categories: Sequence[str] = ("general",),
        time_range: TimeRange | None = None,
        language: str | None = None,
        site_filter: Sequence[str] | None = None,
        max_results: int = 10,
    ) -> list[SearchResult]:
        if site_filter:
            # SearXNG honors site: filters in the query string.
            site_terms = " OR ".join(f"site:{s}" for s in site_filter)
            q = f"({site_terms}) {query}"
        else:
            q = query
        params: dict[str, Any] = {
            "q": q,
            "format": "json",
            "categories": ",".join(categories),
        }
        if time_range:
            params["time_range"] = time_range
        if language:
            params["language"] = language

        async with trusted_client(self.base_url, timeout=20) as c:
            try:
                r = await c.get("/search", params=params)
            except httpx.TimeoutException as e:
                raise ProviderUnavailable("searxng timeout") from e
            except httpx.HTTPError as e:
                raise ProviderUnavailable(f"searxng unreachable: {type(e).__name__}") from e
        if r.status_code == 403:
            raise NotConfigured("searxng returned 403 — enable `json` under search.formats")
        if r.status_code >= 500:
            raise ProviderUnavailable(f"searxng HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError as e:
            raise ProviderError(f"searxng returned non-JSON (HTTP {r.status_code})") from e
        if not isinstance(data, dict):
            raise ProviderError("searxng returned unexpected JSON")
        results = []
        for i, item in enumerate(data.get("results") or []):
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str):
                continue
            results.append(
                SearchResult(
                    url=url,
                    title=str(item.get("title") or "")[:500],
                    snippet=str(item.get("content") or "")[:1000],
                    published_at=_parse_date(item.get("publishedDate")),
                    engines=tuple(item.get("engines") or ()),
                    rank=i + 1,
                )
            )
            if len(results) >= max_results:
                break
        return results

    async def health(self) -> ProviderHealth:
        try:
            async with trusted_client(self.base_url, timeout=5) as c:
                r = await c.get("/", params={"format": "json", "q": "healthcheck"})
            ok = r.status_code < 500
            return ProviderHealth(self.name, ok, f"HTTP {r.status_code}")
        except httpx.HTTPError as e:
            return ProviderHealth(self.name, False, f"unreachable: {type(e).__name__}")


class NullSearch:
    name = "none"

    async def search(
        self,
        query: str,
        *,
        categories: Sequence[str] = ("general",),
        time_range: TimeRange | None = None,
        language: str | None = None,
        site_filter: Sequence[str] | None = None,
        max_results: int = 10,
    ) -> list[SearchResult]:
        raise NotConfigured("search provider disabled")

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "disabled")


def _parse_date(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(v[: len(fmt) + 1 if "T" in fmt else len(fmt)], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(v.rstrip("Z"))
    except ValueError:
        return None
