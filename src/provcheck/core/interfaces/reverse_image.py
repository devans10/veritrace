from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from provcheck.core.interfaces.base import ProviderHealth


@dataclass(frozen=True)
class ImageMatch:
    """A single prior-sighting of an image, keyed by perceptual-hash similarity."""

    phash: str
    page_url: str | None
    first_seen: datetime | None
    similarity: float | None  # 0..1; 1 == identical. None if the provider doesn't score.
    provider: str


@runtime_checkable
class ReverseImageProvider(Protocol):
    """Finds prior sightings of an image across captures.

    v0.1: the local_phash impl is self-building against prior captures. Early
    cases will return empty lists; the investigator records those as
    `no_results` entries rather than evidence.
    """

    name: str

    async def search(
        self, phashes: Sequence[str], *, max_results: int = 20
    ) -> list[ImageMatch]: ...

    async def health(self) -> ProviderHealth: ...
