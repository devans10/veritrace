from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from provcheck.core.interfaces.base import ProviderHealth


@dataclass(frozen=True)
class DomainInfo:
    domain: str
    registered_on: date | None
    expires_on: date | None
    last_changed_on: date | None
    registrar: str | None
    source: str  # the registry/registrar queried


@runtime_checkable
class DomainInfoProvider(Protocol):
    name: str

    async def lookup(self, domain: str) -> DomainInfo: ...

    async def health(self) -> ProviderHealth: ...
