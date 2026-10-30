"""Error-Level Analysis (ELA).

Re-saves the input JPEG at a known quality level, computes the pixel-wise
absolute difference against the original, and returns:

* ``diff_png``             — the diff image (grayscale, scaled to 0..255) as PNG.
* ``mean_abs_difference``  — the mean absolute pixel difference.
* ``max_abs_difference``   — the largest single-pixel difference.

**ELA is informational only.** The scorer caps the ``media.ela`` signal at
weight 0 — ELA produces false positives on heavily-compressed JPEGs and
genuine edits can look identical to compression. We store the diff for the
report but do not use it to move the band.
"""

from __future__ import annotations

import asyncio
import io
import logging

from provcheck.core.interfaces import ELAReading, ProviderError, ProviderHealth

log = logging.getLogger(__name__)


class PillowELA:
    name = "pillow_ela"

    def __init__(self, *, jpeg_quality: int = 90) -> None:
        if not 1 <= jpeg_quality <= 100:
            raise ValueError("jpeg_quality must be 1..100")
        self.quality = jpeg_quality

    async def analyze(self, jpeg: bytes) -> ELAReading:
        return await asyncio.to_thread(self._analyze_sync, jpeg)

    def _analyze_sync(self, jpeg: bytes) -> ELAReading:
        try:
            from PIL import Image, ImageChops
        except ImportError as e:
            raise ProviderError(f"Pillow missing: {e}") from e

        try:
            with Image.open(io.BytesIO(jpeg)) as im:
                rgb = im.convert("RGB")
                buf = io.BytesIO()
                rgb.save(buf, format="JPEG", quality=self.quality)
                buf.seek(0)
                with Image.open(buf) as resaved:
                    resaved.load()
                    diff = ImageChops.difference(rgb, resaved)
        except Exception as e:
            raise ProviderError(f"ELA failed: {type(e).__name__}: {e}") from e

        # Scale the diff into something humans can see in a report. Each
        # extrema entry is a (min, max) tuple per channel; some older Pillow
        # versions return a flat tuple for single-channel images.
        extrema = diff.getextrema()
        if extrema and isinstance(extrema[0], tuple):
            channel_max = max(int(ex[1]) for ex in extrema)
        elif extrema:
            second = extrema[1]
            channel_max = int(second) if not isinstance(second, tuple) else int(second[1])
        else:
            channel_max = 1
        if channel_max > 0:
            diff = diff.point(lambda p: min(255, int(p * 255 / channel_max)))
        grey = diff.convert("L")
        # `getdata` is deprecated in Pillow 14; fall back where it isn't available.
        get_flattened = getattr(grey, "get_flattened_data", None)
        if get_flattened is not None:
            counts = list(get_flattened())
        else:  # pragma: no cover
            counts = list(grey.getdata())
        pixel_count = grey.width * grey.height
        mean = sum(counts) / pixel_count if pixel_count else 0.0

        out = io.BytesIO()
        grey.save(out, format="PNG")
        return ELAReading(
            diff_png=out.getvalue(),
            mean_abs_difference=float(mean),
            max_abs_difference=int(channel_max),
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, f"quality={self.quality}")
