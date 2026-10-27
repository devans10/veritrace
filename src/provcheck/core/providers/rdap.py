"""RDAP domain-info provider.

Looks up a domain's registration metadata via the IANA RDAP bootstrap endpoint
(``https://rdap.iana.org/domain/<domain>``). The bootstrap responds with a 302
redirect to the authoritative TLD RDAP service when it exists; we follow one
redirect to get the actual record. For some TLDs no public RDAP service
exists — those return 404 and we raise ``NotConfigured``.

Dates are parsed from the ``events`` array per RFC 7483.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import httpx

from provcheck.core.interfaces import (
    DomainInfo,
    NotConfigured,
    ProviderError,
    ProviderHealth,
    ProviderUnavailable,
)
from provcheck.core.providers.registrable import registrable_domain
from provcheck.core.security.ssrf import trusted_client

log = logging.getLogger(__name__)


class RDAPDomainInfo:
    name = "rdap"

    def __init__(self, *, bootstrap_url: str) -> None:
        if not bootstrap_url:
            raise NotConfigured("RDAP_BOOTSTRAP_URL is not set")
        self.bootstrap_url = bootstrap_url.rstrip("/")

    async def lookup(self, domain: str) -> DomainInfo:
        reg = registrable_domain(domain) or domain
        async with trusted_client(self.bootstrap_url, timeout=15) as c:
            try:
                r = await c.get(f"/{reg}", follow_redirects=True)
            except httpx.TimeoutException as e:
                raise ProviderUnavailable("rdap timeout") from e
            except httpx.HTTPError as e:
                raise ProviderUnavailable(f"rdap unreachable: {type(e).__name__}") from e
        if r.status_code == 404:
            raise NotConfigured(f"no RDAP record for {reg}")
        if r.status_code == 429 or r.status_code >= 500:
            raise ProviderUnavailable(f"rdap HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError as e:
            raise ProviderError(f"rdap returned non-JSON (HTTP {r.status_code})") from e
        if r.status_code >= 400:
            raise ProviderError(f"rdap HTTP {r.status_code}: {data}")
        if not isinstance(data, dict):
            raise ProviderError("rdap returned unexpected JSON")

        events = data.get("events") or []
        registered = _event_date(events, "registration")
        changed = _event_date(events, "last changed")
        expires = _event_date(events, "expiration")
        registrar = _registrar(data)
        return DomainInfo(
            domain=reg,
            registered_on=registered,
            expires_on=expires,
            last_changed_on=changed,
            registrar=registrar,
            source=str(data.get("port43") or data.get("handle") or r.url.host),
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, f"bootstrap={self.bootstrap_url}")


class NullDomainInfo:
    name = "none"

    async def lookup(self, domain: str) -> DomainInfo:
        raise NotConfigured("domain-info provider disabled")

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "disabled")


def _event_date(events: list[Any], action: str) -> date | None:
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("eventAction") == action:
            ts = ev.get("eventDate")
            if isinstance(ts, str):
                try:
                    return datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
                except ValueError:
                    continue
    return None


def _registrar(data: dict[str, Any]) -> str | None:
    for entity in data.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        roles = entity.get("roles") or []
        if "registrar" not in roles:
            continue
        v = entity.get("vcardArray")
        # vCard is nested: ["vcard", [["version", {}, "text", "4.0"], ["fn", {}, "text", "Name"]]]
        if isinstance(v, list) and len(v) >= 2 and isinstance(v[1], list):
            for field in v[1]:
                if isinstance(field, list) and len(field) >= 4 and field[0] == "fn":
                    return str(field[3])
        return None
    return None
