"""AI-generation detector.

No real detector is enabled in v0.1 — current open detectors produce too many
false positives for the band-only output. The NullDetector raises
``NotConfigured`` so the media investigator records it as an unverified entry
in every case.
"""

from __future__ import annotations

from provcheck.core.interfaces import DetectorResult, NotConfigured, ProviderHealth


class NullDetector:
    name = "none"

    async def classify(self, data: bytes, *, mime: str | None = None) -> DetectorResult:
        raise NotConfigured(
            "no AI-generation detector is enabled in v0.1 — current detectors "
            "are too unreliable for a band-level signal"
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "disabled")
