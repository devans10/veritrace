"""Contract tests for SearXNG and Google Fact Check adapters."""

import httpx
import pytest
import respx

from provcheck.core.interfaces import NotConfigured, ProviderError, ProviderUnavailable
from provcheck.core.providers.google_factcheck import GoogleFactCheck, NullFactCheck
from provcheck.core.providers.searxng import NullSearch, SearXNGSearch

SX = "http://searxng.local:8080"
GC = "https://factchecktools.googleapis.com"


# ----- SearXNG --------------------------------------------------------------


@respx.mock
async def test_searxng_search_parses_results():
    respx.get(f"{SX}/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://a.example/x",
                        "title": "Title A",
                        "content": "snippet A",
                        "engines": ["google"],
                    },
                    {
                        "url": "https://b.example/y",
                        "title": "Title B",
                        "content": "snippet B",
                        "publishedDate": "2026-01-02",
                    },
                ]
            },
        )
    )
    s = SearXNGSearch(base_url=SX)
    out = await s.search("my query", site_filter=["a.example"], max_results=10)
    assert [r.url for r in out] == ["https://a.example/x", "https://b.example/y"]
    assert out[0].engines == ("google",)
    assert out[0].rank == 1


@respx.mock
async def test_searxng_site_filter_in_query():
    route = respx.get(f"{SX}/search").mock(return_value=httpx.Response(200, json={"results": []}))
    await SearXNGSearch(base_url=SX).search(
        "flu vaccine", site_filter=["politifact.com", "snopes.com"]
    )
    q = route.calls.last.request.url.params["q"]
    assert "site:politifact.com" in q and "site:snopes.com" in q
    assert "flu vaccine" in q


@respx.mock
async def test_searxng_403_is_not_configured():
    respx.get(f"{SX}/search").mock(return_value=httpx.Response(403))
    with pytest.raises(NotConfigured):
        await SearXNGSearch(base_url=SX).search("x")


@respx.mock
async def test_searxng_server_error_is_unavailable():
    respx.get(f"{SX}/search").mock(return_value=httpx.Response(503))
    with pytest.raises(ProviderUnavailable):
        await SearXNGSearch(base_url=SX).search("x")


async def test_null_search_raises_not_configured():
    with pytest.raises(NotConfigured):
        await NullSearch().search("x")


# ----- Google Fact Check ----------------------------------------------------


@respx.mock
async def test_factcheck_parses_claims():
    respx.get(f"{GC}/v1alpha1/claims:search").mock(
        return_value=httpx.Response(
            200,
            json={
                "claims": [
                    {
                        "text": "The sky is purple.",
                        "claimant": "A politician",
                        "claimDate": "2026-04-01T00:00:00Z",
                        "claimReview": [
                            {
                                "publisher": {"name": "FactCheck.org", "site": "factcheck.org"},
                                "url": "https://factcheck.org/review/1",
                                "title": "The sky is not purple",
                                "reviewDate": "2026-04-02T00:00:00Z",
                                "textualRating": "False",
                                "languageCode": "en",
                            }
                        ],
                    }
                ]
            },
        )
    )
    hits = await GoogleFactCheck(api_key="K", base_url=GC).search("sky purple")
    assert len(hits) == 1
    h = hits[0]
    assert h.textual_rating == "False"
    assert h.review_publisher == "FactCheck.org"
    assert h.review_url == "https://factcheck.org/review/1"


@respx.mock
async def test_factcheck_401_is_not_configured():
    respx.get(f"{GC}/v1alpha1/claims:search").mock(return_value=httpx.Response(403))
    with pytest.raises(NotConfigured):
        await GoogleFactCheck(api_key="K", base_url=GC).search("x")


@respx.mock
async def test_factcheck_429_is_unavailable():
    respx.get(f"{GC}/v1alpha1/claims:search").mock(return_value=httpx.Response(429))
    with pytest.raises(ProviderUnavailable):
        await GoogleFactCheck(api_key="K", base_url=GC).search("x")


@respx.mock
async def test_factcheck_bogus_body_is_provider_error():
    respx.get(f"{GC}/v1alpha1/claims:search").mock(
        return_value=httpx.Response(200, text="not json")
    )
    with pytest.raises(ProviderError):
        await GoogleFactCheck(api_key="K", base_url=GC).search("x")


def test_factcheck_requires_api_key():
    with pytest.raises(NotConfigured):
        GoogleFactCheck(api_key="", base_url=GC)


async def test_null_factcheck():
    with pytest.raises(NotConfigured):
        await NullFactCheck().search("x")
