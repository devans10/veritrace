"""Pure helpers used by provenance and corroboration."""

from provcheck.core.investigators.text_helpers import (
    distinctive_sentences,
    extract_image_urls,
    extract_links,
    independent_registrable_domains,
    split_sentences,
)


def test_split_sentences():
    out = split_sentences("One. Two! Three? Four.")
    assert out == ["One.", "Two!", "Three?", "Four."]


def test_distinctive_sentences_prefers_specific():
    text = (
        "This is short. "
        "Officials said the Harbor Bridge reopened on 14 March 2026 "
        "after a two-year renovation. "
        "Nothing happened yesterday. "
        "The Mayor of Riverford confirmed 84 million dollars in cost "
        "overruns at the ribbon-cutting."
    )
    picks = distinctive_sentences(text, limit=2)
    assert len(picks) == 2
    # The two information-dense sentences should win over the empty ones.
    assert any("Harbor Bridge reopened on 14 March 2026" in p for p in picks)
    assert any("84 million dollars" in p for p in picks)


def test_distinctive_sentences_respects_min_chars():
    text = "Short one. Short two. Short three."
    assert distinctive_sentences(text, min_chars=60) == []


def test_extract_links_absolute_and_relative():
    html = """
    <a href="/relative">rel</a>
    <a href="https://example.com/x">abs</a>
    <a href="#anchor">anchor</a>
    <a href="mailto:a@b.c">mail</a>
    <a href="javascript:alert(1)">js</a>
    <a href="https://example.com/x">dup</a>
    """
    out = extract_links(html, base_url="https://news.example/article")
    assert out == ["https://news.example/relative", "https://example.com/x"]


def test_extract_image_urls_skips_data_and_dedupes():
    html = """
    <img src="/a.png">
    <img src="https://cdn.example/b.jpg">
    <img src="data:image/png;base64,...">
    <img src="/a.png">
    """
    out = extract_image_urls(html, base_url="https://news.example/")
    assert out == ["https://news.example/a.png", "https://cdn.example/b.jpg"]


def test_independent_registrable_domains():
    out = independent_registrable_domains(
        [
            "https://www.bbc.co.uk/news/story-1",
            "https://news.bbc.co.uk/story-2",  # same registrable domain
            "https://apnews.com/x",
            "https://www.reuters.com/y",
            "https://subdomain.reuters.com/z",  # same registrable domain
            "not a url",
        ]
    )
    assert out == ["bbc.co.uk", "apnews.com", "reuters.com"]
