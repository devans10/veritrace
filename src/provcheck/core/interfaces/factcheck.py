from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from provcheck.core.interfaces.base import ProviderHealth


@dataclass(frozen=True)
class ClaimReviewHit:
    """A single ClaimReview record (per schema.org/ClaimReview).

    Textual rating is passed through verbatim; direction is derived later from a
    conservative lexicon, not from the raw rating value.
    """

    claim_text: str
    claimant: str | None
    claim_date: date | None
    review_url: str
    review_publisher: str | None
    review_publisher_site: str | None
    review_title: str | None
    review_date: date | None
    textual_rating: str
    language_code: str | None = None


@runtime_checkable
class FactCheckProvider(Protocol):
    name: str

    async def search(
        self, claim_text: str, *, language: str | None = None, max_results: int = 10
    ) -> list[ClaimReviewHit]: ...

    async def health(self) -> ProviderHealth: ...
