"""Conservative mapping from fact-check textual ratings to (direction, strength).

Textual ratings vary wildly across publishers; this module maps a small, well-known
set of English rating phrases. Anything that doesn't match returns `neutral` and
the caller records an unverified entry (``no_results`` / ``unsupported_platform``)
rather than silently coercing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Direction = Literal["supports", "undermines", "neutral"]


@dataclass(frozen=True)
class Mapping:
    direction: Direction
    strength: float  # 0..1


# Phrases are matched after lower-casing and stripping punctuation; longer phrases
# are checked first so "mostly false" doesn't match "false".
_RULES: list[tuple[str, Mapping]] = [
    # strongly false
    ("pants on fire", Mapping("undermines", 1.0)),
    ("pants-on-fire", Mapping("undermines", 1.0)),
    ("fabricated", Mapping("undermines", 1.0)),
    ("fabrication", Mapping("undermines", 1.0)),
    ("hoax", Mapping("undermines", 1.0)),
    ("fake", Mapping("undermines", 0.9)),
    # false
    ("false", Mapping("undermines", 0.9)),
    ("incorrect", Mapping("undermines", 0.8)),
    ("inaccurate", Mapping("undermines", 0.7)),
    ("not true", Mapping("undermines", 0.8)),
    ("debunked", Mapping("undermines", 0.9)),
    ("misleading", Mapping("undermines", 0.7)),
    ("misinformation", Mapping("undermines", 0.8)),
    ("misrepresents", Mapping("undermines", 0.7)),
    # partial
    ("mostly false", Mapping("undermines", 0.6)),
    ("partly false", Mapping("undermines", 0.5)),
    ("mixture", Mapping("neutral", 0.3)),
    ("mixed", Mapping("neutral", 0.3)),
    ("half true", Mapping("neutral", 0.3)),
    ("mostly true", Mapping("supports", 0.6)),
    # true
    ("true", Mapping("supports", 0.9)),
    ("correct", Mapping("supports", 0.8)),
    ("accurate", Mapping("supports", 0.8)),
    ("confirmed", Mapping("supports", 0.8)),
    ("verified", Mapping("supports", 0.8)),
    # satire (handled separately but useful as a hint)
    ("satire", Mapping("neutral", 0.2)),
    ("parody", Mapping("neutral", 0.2)),
]
# Longer phrases first so compound ratings win.
_RULES.sort(key=lambda r: -len(r[0]))


def map_rating(textual_rating: str) -> Mapping:
    """Return (direction, strength). Unknown ratings → neutral/0."""
    rating = textual_rating.lower()
    for phrase, mapping in _RULES:
        if phrase in rating:
            return mapping
    return Mapping("neutral", 0.0)
