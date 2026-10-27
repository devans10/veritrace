"""Upload validation."""

import io

import pytest
from PIL import Image

from provcheck.core.security.uploads import UploadRejected, sniff_mime, validate_upload


def _jpeg(size: int = 64) -> bytes:
    img = Image.new("RGB", (size, size), color=(220, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _png() -> bytes:
    img = Image.new("RGB", (10, 10), color=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


ALLOWED = ["image/jpeg", "image/png", "image/webp", "image/gif", "video/mp4"]


def test_sniff_jpeg():
    mime, ext = sniff_mime(_jpeg())
    assert mime == "image/jpeg"
    assert ext == "jpg"


def test_sniff_png():
    mime, _ = sniff_mime(_png())
    assert mime == "image/png"


def test_sniff_unknown_returns_none():
    assert sniff_mime(b"this is just some text") is None


def test_validate_rejects_empty():
    with pytest.raises(UploadRejected, match="empty"):
        validate_upload(b"", max_bytes=1024, allowed_mimes=ALLOWED)


def test_validate_rejects_too_large():
    data = _jpeg() + b"\x00" * 10_000
    with pytest.raises(UploadRejected, match="exceeds"):
        validate_upload(data, max_bytes=1024, allowed_mimes=ALLOWED)


def test_validate_rejects_unknown_format():
    with pytest.raises(UploadRejected, match="magic bytes"):
        validate_upload(
            b"<!doctype html><p>hi</p>" * 10, max_bytes=1_000_000, allowed_mimes=ALLOWED
        )


def test_validate_rejects_disallowed_mime():
    with pytest.raises(UploadRejected, match="not allowed"):
        validate_upload(_jpeg(), max_bytes=1_000_000, allowed_mimes=["image/webp"])


def test_validate_accepts_jpeg_and_builds_preview():
    v = validate_upload(_jpeg(size=1000), max_bytes=10_000_000, allowed_mimes=ALLOWED)
    assert v.detected_mime == "image/jpeg"
    assert v.extension == "jpg"
    assert v.preview_png is not None
    # Preview must be smaller than the original big JPEG.
    assert len(v.preview_png) < len(v.data)


def test_validate_rejects_polyglot_declared_as_image():
    # HTML content with image/jpeg claimed by caller — sniffing ignores the claim.
    with pytest.raises(UploadRejected, match="magic bytes"):
        validate_upload(
            b"<script>alert(1)</script>" * 50, max_bytes=1_000_000, allowed_mimes=ALLOWED
        )
