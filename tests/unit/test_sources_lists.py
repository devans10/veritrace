"""Seed-list loader + lookup semantics."""

from provcheck.core.providers.sources import load_source_lists


def test_curated_lists_load():
    sl = load_source_lists("config/sources")
    assert "theonion.com" in sl.satire
    assert "apnews.com" in sl.factcheckers or "apnews.com" in sl.reputation
    assert "reuters.com" in sl.reputation
    assert sl.reputation["apnews.com"].tier == "wire"


def test_url_lookup_normalises_to_registrable_domain():
    sl = load_source_lists("config/sources")
    assert sl.is_satire("https://www.theonion.com/article/123") is not None
    assert sl.is_reputable("https://news.bbc.co.uk/story") is not None
    assert sl.is_satire("https://not-satire.example") is None


def test_each_entry_cites_its_source():
    sl = load_source_lists("config/sources")
    for name, entries in (
        ("satire", sl.satire),
        ("factcheckers", sl.factcheckers),
        ("reputation", sl.reputation),
    ):
        for domain, entry in entries.items():
            assert entry.source.strip(), f"{name}:{domain} has no basis"


def test_factchecker_domains_sorted():
    sl = load_source_lists("config/sources")
    assert sl.factchecker_domains == sorted(sl.factchecker_domains)
