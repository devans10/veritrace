"""Registrable-domain extraction.

Uses ``tldextract`` with its bundled public suffix list snapshot (no live
fetch — ``include_psl_private_domains=False`` plus a cached snapshot keeps
behaviour deterministic across restarts).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import tldextract


@lru_cache(maxsize=1)
def _extractor() -> Any:
    # suffix_list_urls=() + fallback_to_snapshot=True → never fetches live.
    return tldextract.TLDExtract(suffix_list_urls=(), fallback_to_snapshot=True)


def registrable_domain(url_or_host: str) -> str | None:
    """Return the registrable domain (e.g., 'bbc.co.uk' for 'news.bbc.co.uk/path').
    Returns None on obvious non-domains (IPs, empty, invalid)."""
    if not url_or_host:
        return None
    r = _extractor()(url_or_host)
    if not r.domain or not r.suffix:
        return None
    return f"{r.domain}.{r.suffix}".lower()
