"""Upload validation.

* Enforces a size cap *before* reading the whole payload.
* Sniffs the actual MIME type from the first few bytes (``filetype``), ignoring
  the client-supplied ``Content-Type`` which is attacker-controlled.
* Rejects anything outside the configured allow-list.
* Produces a small re-encoded preview for images so the UI can safely render
  what was uploaded without ever serving the original bytes with their
  claimed Content-Type.

The caller is responsible for never serving uploads back with the attacker-
supplied content-type header (see ``services/api/app.py``).
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterable
from dataclasses import dataclass

log = logging.getLogger(__name__)


class UploadRejected(Exception):
    """Raised when an upload fails validation. Message is user-visible."""


@dataclass(frozen=True)
class ValidatedUpload:
    data: bytes
    detected_mime: str
    extension: str
    size: int
    preview_png: bytes | None


def sniff_mime(data: bytes) -> tuple[str, str] | None:
    """Return (mime, extension) from magic bytes, or None if the format is unknown."""
    try:
        import filetype
    except ImportError as e:  # pragma: no cover
        raise UploadRejected(f"filetype library unavailable: {e}") from e
    kind = filetype.guess(data)
    if kind is None:
        return None
    return kind.mime, kind.extension


def _preview_for_image(data: bytes, *, max_dim: int = 800) -> bytes | None:
    """Re-encode as PNG at a bounded size. Returns None on failure."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(data)) as im:
            rgb = im.convert("RGB")
            rgb.thumbnail((max_dim, max_dim))
            buf = io.BytesIO()
            rgb.save(buf, format="PNG")
            return buf.getvalue()
    except Exception as e:
        log.info("preview re-encode failed: %s", e)
        return None


def validate_upload(
    data: bytes,
    *,
    max_bytes: int,
    allowed_mimes: Iterable[str],
) -> ValidatedUpload:
    size = len(data)
    if size == 0:
        raise UploadRejected("upload is empty")
    if size > max_bytes:
        raise UploadRejected(f"upload exceeds the {max_bytes}-byte limit ({size})")
    sniffed = sniff_mime(data)
    if sniffed is None:
        raise UploadRejected("could not identify file format from magic bytes")
    mime, extension = sniffed
    allowed = set(allowed_mimes)
    if mime not in allowed:
        raise UploadRejected(f"content type {mime!r} is not allowed; allowed: {sorted(allowed)}")
    preview = _preview_for_image(data) if mime.startswith("image/") else None
    return ValidatedUpload(
        data=data,
        detected_mime=mime,
        extension=extension,
        size=size,
        preview_png=preview,
    )
