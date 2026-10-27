"""Shared helpers for the provenance / corroboration investigators.

Pure functions; no I/O. The HTML parsing uses :mod:`selectolax` which is a
lean, C-backed parser — same as the capture service's text extraction.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urljoin, urlparse

from provcheck.core.providers.registrable import registrable_domain

# A "distinctive" sentence has enough specific tokens that an exact-phrase
# search is likely to return relevant results. We score by length and
# presence of named-entity-ish tokens (capitalised words, numbers, dates).
_NAMED_ENTITY = re.compile(r"\b[A-Z][a-zA-Z'\-]{2,}\b")
_NUMBER = re.compile(r"\b\d[\d,.]*\b")
_DATE = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\b")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])")


def split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    out: list[str] = []
    for raw in _SENTENCE_SPLIT.split(text):
        s = re.sub(r"\s+", " ", raw).strip()
        if s:
            out.append(s)
    return out


def distinctive_sentences(text: str, *, limit: int = 3, min_chars: int = 60) -> list[str]:
    """Pick up to ``limit`` sentences likely to be found verbatim online.

    Scores a sentence by length plus hits on capitalised tokens, numbers and
    month names. Longer sentences are capped at 300 chars so they still fit
    in an exact-phrase query. Ties are broken by original order.
    """
    scored: list[tuple[int, int, str]] = []
    for i, s in enumerate(split_sentences(text)):
        if len(s) < min_chars:
            continue
        if len(s) > 300:
            s = s[:300].rsplit(" ", 1)[0]
        score = (
            min(len(s), 300)
            + 10 * len(_NAMED_ENTITY.findall(s))
            + 5 * len(_NUMBER.findall(s))
            + 5 * len(_DATE.findall(s))
        )
        scored.append((-score, i, s))
    scored.sort()
    return [s for _, _, s in scored[:limit]]


def extract_links(html: str, base_url: str | None = None) -> list[str]:
    """Return absolute hrefs of outbound links. Dedupes while preserving order."""
    try:
        from selectolax.lexbor import LexborHTMLParser as HTMLParser
    except ImportError:
        return _regex_hrefs(html, base_url)
    seen: set[str] = set()
    out: list[str] = []
    tree = HTMLParser(html)
    for node in tree.css("a[href]"):
        href = node.attributes.get("href") or ""
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        absolute = urljoin(base_url, href) if base_url else href
        if absolute in seen:
            continue
        if not absolute.startswith(("http://", "https://")):
            continue
        seen.add(absolute)
        out.append(absolute)
    return out


def extract_image_urls(html: str, base_url: str | None = None) -> list[str]:
    try:
        from selectolax.lexbor import LexborHTMLParser as HTMLParser
    except ImportError:
        return []
    out: list[str] = []
    seen: set[str] = set()
    tree = HTMLParser(html)
    for node in tree.css("img[src]"):
        src = node.attributes.get("src") or ""
        if not src or src.startswith("data:"):
            continue
        absolute = urljoin(base_url, src) if base_url else src
        if not absolute.startswith(("http://", "https://")):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(absolute)
    return out


def independent_registrable_domains(urls: Iterable[str]) -> list[str]:
    """Count distinct registrable domains across ``urls``, preserving first-seen order."""
    seen: list[str] = []
    for url in urls:
        reg = registrable_domain(_host(url))
        if reg is None:
            continue
        if reg not in seen:
            seen.append(reg)
    return seen


def _host(url: str) -> str:
    try:
        p = urlparse(url)
        return p.hostname or url
    except ValueError:
        return url


def _regex_hrefs(html: str, base_url: str | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
        href = match.group(1)
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        absolute = urljoin(base_url, href) if base_url else href
        if not absolute.startswith(("http://", "https://")):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(absolute)
    return out
