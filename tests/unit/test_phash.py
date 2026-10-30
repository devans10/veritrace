"""pHash determinism + Hamming helper."""

import io

from PIL import Image

from provcheck.core.providers.local_phash import compute_phash, hamming


def _png_bytes(color: tuple[int, int, int], size: int = 64) -> bytes:
    img = Image.new("RGB", (size, size), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _patterned_png(stripe: int, color: tuple[int, int, int], size: int = 64) -> bytes:
    """Horizontal-stripe pattern; pHash captures the structure."""
    img = Image.new("RGB", (size, size), color=(255, 255, 255))
    pixels = img.load()
    for y in range(size):
        if (y // stripe) % 2:
            for x in range(size):
                pixels[x, y] = color
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_hamming_identical_is_zero():
    assert hamming("ffff" * 4, "ffff" * 4) == 0


def test_hamming_counts_bit_differences():
    assert hamming("0", "f") == 4  # 0b0000 vs 0b1111


def test_hamming_mismatched_sizes_is_max():
    assert hamming("abcd", "ab") == 64


def test_phash_deterministic():
    a = _png_bytes((200, 50, 50))
    b = _png_bytes((200, 50, 50))
    assert compute_phash(a) == compute_phash(b)


def test_phash_differs_for_different_images():
    # pHash captures *structure* not color — solid-color images of the same
    # size hash the same regardless of color. Use different stripe patterns.
    a = compute_phash(_patterned_png(4, (0, 0, 0)))
    b = compute_phash(_patterned_png(16, (0, 0, 0)))
    assert a != b
    assert hamming(a, b) >= 1


def test_phash_returns_none_for_garbage():
    assert compute_phash(b"not an image at all") is None
