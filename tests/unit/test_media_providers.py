"""EXIF reader + ELA provider."""

import io

import piexif
import pytest
from PIL import Image

from provcheck.core.interfaces import ProviderError
from provcheck.core.providers.ela import PillowELA
from provcheck.core.providers.exif import PillowExifReader


def _jpeg_without_exif() -> bytes:
    img = Image.new("RGB", (32, 32), color=(100, 100, 100))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _jpeg_with_exif(
    make: str = "Canon",
    model: str = "EOS 5D",
    dt: str = "2026:03:14 09:30:00",
    software: str = "veritrace-tests",
) -> bytes:
    img = Image.new("RGB", (32, 32), color=(50, 160, 200))
    exif_dict = {
        "0th": {
            piexif.ImageIFD.Make: make.encode(),
            piexif.ImageIFD.Model: model.encode(),
            piexif.ImageIFD.Software: software.encode(),
        },
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: dt.encode(),
            piexif.ExifIFD.DateTimeDigitized: dt.encode(),
        },
        "GPS": {},
        "1st": {},
        "thumbnail": None,
    }
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=piexif.dump(exif_dict))
    return buf.getvalue()


async def test_exif_reads_structured_fields():
    r = PillowExifReader()
    reading = await r.read(_jpeg_with_exif())
    assert reading.has_exif
    assert reading.mime == "image/jpeg"
    assert reading.width == 32 and reading.height == 32
    assert reading.camera_make == "Canon"
    assert reading.camera_model == "EOS 5D"
    assert reading.datetime_original.isoformat() == "2026-03-14T09:30:00"
    assert reading.software == "veritrace-tests"
    assert reading.gps_present is False


async def test_exif_handles_no_metadata():
    r = PillowExifReader()
    reading = await r.read(_jpeg_without_exif())
    assert reading.has_exif is False
    assert reading.camera_make is None


async def test_exif_rejects_non_image():
    r = PillowExifReader()
    with pytest.raises(ProviderError, match="could not read image"):
        await r.read(b"not an image")


async def test_ela_produces_diff_and_metrics():
    e = PillowELA(jpeg_quality=85)
    reading = await e.analyze(_jpeg_with_exif())
    assert reading.diff_png.startswith(b"\x89PNG")
    assert reading.mean_abs_difference >= 0.0
    assert 0 <= reading.max_abs_difference <= 255


async def test_ela_rejects_non_image():
    e = PillowELA()
    with pytest.raises(ProviderError, match="ELA failed"):
        await e.analyze(b"not an image")
