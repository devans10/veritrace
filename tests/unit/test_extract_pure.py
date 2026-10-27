"""Pure helpers in the extract stage: normalization, span finding, schema shape."""

import pytest
from pydantic import ValidationError

from provcheck.core.pipeline.extract import (
    ExtractedClaim,
    ExtractedClaims,
    _build_user_prompt,
    _find_span,
    _norm,
)


def test_norm_collapses_whitespace_and_lowers():
    assert _norm("  Hello\n\n WORLD\t! ") == "hello world !"


def test_find_span_hits_exact():
    text = "Officials said the bridge reopened on Tuesday after two years."
    pos = _find_span("the bridge reopened on tuesday", text)
    assert pos is not None
    start, end = pos
    assert start >= 0 and end > start


def test_find_span_tolerates_whitespace_differences():
    text = "The   mayor\nconfirmed   84 million dollars."
    assert _find_span("The mayor confirmed 84 million dollars.", text) is not None


def test_find_span_returns_none_for_hallucinated_claim():
    text = "Officials said the bridge reopened on Tuesday."
    # Fully invented facts must not match — this is the injection defense.
    assert _find_span("The president signed a treaty with Mars.", text) is None


def test_find_span_empty_returns_none():
    assert _find_span("", "any text") is None


def test_user_prompt_fences_content():
    out = _build_user_prompt("hello world")
    assert out.startswith("<content>\n")
    assert out.endswith("\n</content>")


def test_user_prompt_truncates_at_cap():
    text = "a" * 50_000
    out = _build_user_prompt(text, max_chars=1000)
    # The fenced body is capped, not the overall string.
    assert out.count("a") == 1000


def test_extracted_claim_schema_rejects_out_of_range_scores():
    with pytest.raises(ValidationError):
        ExtractedClaim(text="x", source_span="x", checkworthiness=1.5)


def test_extracted_claims_has_default_empty_list():
    cs = ExtractedClaims()
    assert cs.claims == []


def test_extracted_claims_caps_at_ten():
    with pytest.raises(ValidationError):
        ExtractedClaims(claims=[ExtractedClaim(text=f"c{i}", source_span="x") for i in range(11)])
