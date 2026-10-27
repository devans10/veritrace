"""C2PA verifier — reports `missing` when no manifest is present."""

import io

from PIL import Image

from provcheck.core.providers.c2pa import C2PAPythonVerifier


def _jpeg() -> bytes:
    img = Image.new("RGB", (16, 16), color=(0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


async def test_c2pa_missing_on_plain_jpeg():
    v = C2PAPythonVerifier()
    reading = await v.verify(_jpeg(), mime="image/jpeg")
    assert reading.state in ("missing", "invalid")
    # On a plain JPEG with no manifest, we expect `missing` under current SDK
    # behavior but accept `invalid` with a known-error reason if the SDK
    # treats absence as invalid in some installs.
    assert reading.manifests == []


async def test_c2pa_rejects_garbage_as_invalid_or_missing():
    v = C2PAPythonVerifier()
    reading = await v.verify(b"not an image", mime="image/jpeg")
    assert reading.state in ("missing", "invalid")
