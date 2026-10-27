"""EXIF reader (Pillow-only).

Returns a structured :class:`ExifReading`. Dates are parsed from
``DateTimeOriginal`` / ``DateTimeDigitized`` (EXIF uses the format
``YYYY:MM:DD HH:MM:SS``). GPS presence is a boolean — coordinates are
intentionally NOT returned, to avoid handling user geolocation unnecessarily.

Exif tags are decoded via ``PIL.ExifTags.TAGS`` which is Pillow's authoritative
tag map.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from typing import Any

from provcheck.core.interfaces import ExifReading, ProviderError, ProviderHealth

log = logging.getLogger(__name__)


def _parse_exif_dt(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        return datetime.strptime(v.strip(), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        except ValueError:
            return None


class PillowExifReader:
    name = "pillow_exif"

    async def read(self, data: bytes, *, mime: str | None = None) -> ExifReading:
        try:
            from PIL import ExifTags, Image
        except ImportError as e:
            raise ProviderError(f"Pillow missing: {e}") from e

        try:
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                width, height = im.size
                mime_detected = Image.MIME.get(im.format or "", "application/octet-stream")
                raw_exif = im.getexif()
                # Pillow splits EXIF into the primary IFD (0th) and sub-IFDs; most
                # user-level tags (DateTimeOriginal, LensModel, etc.) live in the
                # Exif IFD at tag 0x8769.
                exif_subifd: dict[Any, Any] = (
                    dict(raw_exif.get_ifd(0x8769)) if raw_exif else {}
                )
                gps_block: dict[Any, Any] = (
                    dict(raw_exif.get_ifd(0x8825)) if raw_exif else {}
                )
        except Exception as e:
            raise ProviderError(f"could not read image: {e}") from e

        exif: dict[str, Any] = (
            {ExifTags.TAGS.get(k, str(k)): v for k, v in dict(raw_exif).items()}
            if raw_exif
            else {}
        )
        for k, v in exif_subifd.items():
            exif[ExifTags.TAGS.get(k, str(k))] = v
        gps_present = bool(gps_block)

        return ExifReading(
            mime=mime_detected,
            width=width,
            height=height,
            camera_make=_s(exif.get("Make")),
            camera_model=_s(exif.get("Model")),
            datetime_original=_parse_exif_dt(exif.get("DateTimeOriginal")),
            datetime_digitized=_parse_exif_dt(exif.get("DateTimeDigitized")),
            software=_s(exif.get("Software")),
            gps_present=gps_present,
            raw={k: _s(v) for k, v in exif.items() if k in _KEEP_RAW},
            has_exif=bool(exif) or bool(exif_subifd),
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "")


def _s(v: Any) -> Any:
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8", errors="replace").strip("\x00").strip()
        except Exception:
            return None
    if isinstance(v, str):
        return v.strip("\x00").strip()
    return v


# Tags worth keeping in the audit log; others get dropped to keep rows small.
_KEEP_RAW = frozenset(
    {
        "Make",
        "Model",
        "Software",
        "DateTime",
        "DateTimeOriginal",
        "DateTimeDigitized",
        "LensMake",
        "LensModel",
        "Orientation",
        "ExifVersion",
        "ColorSpace",
        "Artist",
        "Copyright",
    }
)
