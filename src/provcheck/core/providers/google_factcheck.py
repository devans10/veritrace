"""Google Fact Check Tools adapter.

``GET https://factchecktools.googleapis.com/v1alpha1/claims:search`` with API
key auth (``?key=...``). The schema.org ``ClaimReview`` records are mapped to
:class:`ClaimReviewHit` with the textual rating passed through verbatim; the
caller derives direction (supports / undermines / neutral) from a conservative
lexicon so a vague or unmapped rating remains neutral.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import httpx

from provcheck.core.interfaces import (
    ClaimReviewHit,
    NotConfigured,
    ProviderError,
    ProviderHealth,
    ProviderUnavailable,
)
from provcheck.core.security.ssrf import trusted_client

log = logging.getLogger(__name__)


class GoogleFactCheck:
    name = "google"

    def __init__(self, *, api_key: str, base_url: str) -> None:
        if not api_key:
            raise NotConfigured("GOOGLE_API_KEY is not set")
        if not base_url:
            raise NotConfigured("FACTCHECK_BASE_URL is not set")
        self._key = api_key
        self.base_url = base_url.rstrip("/")

    async def search(
        self, claim_text: str, *, language: str | None = None, max_results: int = 10
    ) -> list[ClaimReviewHit]:
        params: dict[str, Any] = {
            "key": self._key,
            "query": claim_text,
            "pageSize": max(1, min(max_results, 50)),
        }
        if language:
            params["languageCode"] = language

        async with trusted_client(self.base_url, timeout=20) as c:
            try:
                r = await c.get("/v1alpha1/claims:search", params=params)
            except httpx.TimeoutException as e:
                raise ProviderUnavailable("google factcheck timeout") from e
            except httpx.HTTPError as e:
                raise ProviderUnavailable(
                    f"google factcheck unreachable: {type(e).__name__}"
                ) from e
        if r.status_code in (401, 403):
            raise NotConfigured(f"google factcheck rejected key (HTTP {r.status_code})")
        if r.status_code == 429 or r.status_code >= 500:
            raise ProviderUnavailable(f"google factcheck HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError as e:
            raise ProviderError(f"google factcheck returned non-JSON (HTTP {r.status_code})") from e
        if r.status_code >= 400:
            raise ProviderError(f"google factcheck HTTP {r.status_code}: {data}")
        hits: list[ClaimReviewHit] = []
        for claim in data.get("claims") or []:
            if not isinstance(claim, dict):
                continue
            text = str(claim.get("text") or "")
            claimant = claim.get("claimant")
            claim_date = _parse_iso_date(claim.get("claimDate"))
            for review in claim.get("claimReview") or []:
                if not isinstance(review, dict):
                    continue
                url = review.get("url")
                if not isinstance(url, str):
                    continue
                publisher = review.get("publisher") or {}
                hits.append(
                    ClaimReviewHit(
                        claim_text=text,
                        claimant=str(claimant) if claimant else None,
                        claim_date=claim_date,
                        review_url=url,
                        review_publisher=(
                            str(publisher.get("name")) if publisher.get("name") else None
                        ),
                        review_publisher_site=(
                            str(publisher.get("site")) if publisher.get("site") else None
                        ),
                        review_title=(str(review.get("title")) if review.get("title") else None),
                        review_date=_parse_iso_date(review.get("reviewDate")),
                        textual_rating=str(review.get("textualRating") or ""),
                        language_code=(
                            str(review.get("languageCode")) if review.get("languageCode") else None
                        ),
                    )
                )
                if len(hits) >= max_results:
                    return hits
        return hits

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, bool(self._key), "configured" if self._key else "no key")


class NullFactCheck:
    name = "none"

    async def search(
        self, claim_text: str, *, language: str | None = None, max_results: int = 10
    ) -> list[ClaimReviewHit]:
        raise NotConfigured("fact-check provider disabled")

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "disabled")


def _parse_iso_date(v: Any) -> date | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        return datetime.fromisoformat(v.rstrip("Z")).date()
    except ValueError:
        return None
