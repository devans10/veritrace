"""Local pHash-based reverse image provider.

Self-building index: whenever a capture produces image artifacts, their
pHashes are stored on ``artifacts.phash``. The provider's ``search`` queries
across every prior capture's pHashes for matches within Hamming distance
``similarity_threshold``.

Early cases (empty index) return no matches; the investigator records those
as ``no_results`` entries rather than evidence.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from provcheck.core.interfaces import (
    ImageMatch,
    NotConfigured,
    ProviderHealth,
)

log = logging.getLogger(__name__)


def _hex_to_bits(h: str) -> int:
    return int(h, 16) if h else 0


def hamming(a: str, b: str) -> int:
    """Hamming distance between two equal-length hex pHashes."""
    if not a or not b or len(a) != len(b):
        return 64  # treat mismatched sizes as "max distance"
    return bin(_hex_to_bits(a) ^ _hex_to_bits(b)).count("1")


def compute_phash(image_bytes: bytes) -> str | None:
    """Return a hex pHash for ``image_bytes``, or None if the image is unreadable."""
    try:
        import imagehash
        from PIL import Image
        from PIL.Image import DecompressionBombError
    except ImportError as e:
        raise NotConfigured("imagehash/pillow not installed") from e
    try:
        import io

        with Image.open(io.BytesIO(image_bytes)) as im:
            im.load()
            return str(imagehash.phash(im))
    except (DecompressionBombError, OSError, ValueError) as e:
        log.info("skipping unreadable image: %s", e)
        return None


class LocalPHashReverseImage:
    """Queries the artifacts table for pHashes within a Hamming distance."""

    name = "local_phash"

    def __init__(self, *, similarity_threshold: int = 8) -> None:
        self.threshold = int(similarity_threshold)

    async def search(self, phashes: Sequence[str], *, max_results: int = 20) -> list[ImageMatch]:
        from provcheck.core.db.models import Artifact, Capture
        from provcheck.core.db.session import session_scope

        def _query() -> list[ImageMatch]:
            matches: list[ImageMatch] = []
            with session_scope() as s:
                rows = (
                    s.query(Artifact.phash, Capture.final_url, Capture.fetched_at)
                    .join(Capture, Capture.case_id == Artifact.case_id)
                    .filter(Artifact.phash.isnot(None))
                    .all()
                )
            for probe in phashes:
                for row in rows:
                    stored_hash, final_url, fetched_at = row[0], row[1], row[2]
                    if not stored_hash or stored_hash == probe:
                        continue
                    d = hamming(probe, stored_hash)
                    if d <= self.threshold:
                        matches.append(
                            ImageMatch(
                                phash=stored_hash,
                                page_url=final_url,
                                first_seen=fetched_at,
                                similarity=1.0 - (d / 64.0),
                                provider="local_phash",
                            )
                        )
                        if len(matches) >= max_results:
                            return matches
            return matches

        return await asyncio.to_thread(_query)

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, f"threshold={self.threshold}")


class NullReverseImage:
    name = "none"

    async def search(self, phashes: Sequence[str], *, max_results: int = 20) -> list[ImageMatch]:
        raise NotConfigured("reverse image provider disabled")

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "disabled")
