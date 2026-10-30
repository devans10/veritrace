"""RDAP + Wayback CDX contract tests."""

import httpx
import pytest
import respx

from provcheck.core.interfaces import NotConfigured, ProviderError, ProviderUnavailable
from provcheck.core.providers.archive import WaybackArchive
from provcheck.core.providers.rdap import RDAPDomainInfo

RDAP = "https://rdap.iana.org/domain"
WB = "https://web.archive.org"


# ----- RDAP -----------------------------------------------------------------


@respx.mock
async def test_rdap_lookup_parses_registration_dates():
    respx.get(f"{RDAP}/example.com").mock(
        return_value=httpx.Response(
            200,
            json={
                "events": [
                    {"eventAction": "registration", "eventDate": "1995-08-14T04:00:00Z"},
                    {"eventAction": "last changed", "eventDate": "2024-01-02T00:00:00Z"},
                    {"eventAction": "expiration", "eventDate": "2027-08-13T04:00:00Z"},
                ],
                "entities": [
                    {
                        "roles": ["registrar"],
                        "vcardArray": [
                            "vcard",
                            [
                                ["version", {}, "text", "4.0"],
                                ["fn", {}, "text", "Example Registrar, LLC"],
                            ],
                        ],
                    }
                ],
            },
        )
    )
    info = await RDAPDomainInfo(bootstrap_url=RDAP).lookup("https://example.com/path")
    assert info.domain == "example.com"
    assert info.registered_on.isoformat() == "1995-08-14"
    assert info.last_changed_on.isoformat() == "2024-01-02"
    assert info.expires_on.isoformat() == "2027-08-13"
    assert info.registrar == "Example Registrar, LLC"


@respx.mock
async def test_rdap_404_is_not_configured():
    respx.get(f"{RDAP}/no-such-tld.weird").mock(return_value=httpx.Response(404))
    with pytest.raises(NotConfigured):
        await RDAPDomainInfo(bootstrap_url=RDAP).lookup("no-such-tld.weird")


@respx.mock
async def test_rdap_server_error_is_unavailable():
    respx.get(f"{RDAP}/example.com").mock(return_value=httpx.Response(503))
    with pytest.raises(ProviderUnavailable):
        await RDAPDomainInfo(bootstrap_url=RDAP).lookup("example.com")


# ----- Wayback CDX ----------------------------------------------------------


def _wb() -> WaybackArchive:
    return WaybackArchive(base_url=WB, access_key="AK", secret_key="SK")


@respx.mock
async def test_cdx_history_parses_rows():
    respx.get(f"{WB}/cdx/search/cdx").mock(
        return_value=httpx.Response(
            200,
            json=[
                ["timestamp", "original", "statuscode", "digest"],
                ["20240115120000", "https://example.com/a", "200", "D1"],
                ["20250302101500", "https://example.com/a", "200", "D2"],
            ],
        )
    )
    history = await _wb().history("https://example.com/a")
    assert len(history) == 2
    assert history[0].captured_at.year == 2024
    assert history[0].archive_url.startswith(f"{WB}/web/20240115120000/")
    assert history[0].digest == "D1"


@respx.mock
async def test_cdx_empty_body_returns_empty_list():
    respx.get(f"{WB}/cdx/search/cdx").mock(return_value=httpx.Response(200, text=""))
    assert await _wb().history("https://example.com/") == []


@respx.mock
async def test_cdx_rate_limit_is_unavailable():
    respx.get(f"{WB}/cdx/search/cdx").mock(return_value=httpx.Response(429))
    with pytest.raises(ProviderUnavailable):
        await _wb().history("https://example.com/")


@respx.mock
async def test_cdx_non_json_is_provider_error():
    respx.get(f"{WB}/cdx/search/cdx").mock(return_value=httpx.Response(200, text="not json"))
    with pytest.raises(ProviderError):
        await _wb().history("https://example.com/")
