from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from provcheck.core.interfaces.base import ProviderHealth

TimeRange = Literal["day", "month", "year"]


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    snippet: str
    published_at: datetime | None = None
    engines: tuple[str, ...] = ()
    rank: int = 0


@runtime_checkable
class SearchProvider(Protocol):
    name: str

    async def search(
        self,
        query: str,
        *,
        categories: Sequence[str] = ("general",),
        time_range: TimeRange | None = None,
        language: str | None = None,
        site_filter: Sequence[str] | None = None,
        max_results: int = 10,
    ) -> list[SearchResult]: ...

    async def health(self) -> ProviderHealth: ...
