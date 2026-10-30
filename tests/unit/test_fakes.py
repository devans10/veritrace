"""Fakes smoke tests: deterministic output and schema compatibility."""

from pydantic import BaseModel

from provcheck.core.interfaces import ClaimReviewHit, SearchResult
from provcheck.core.providers.fake import FakeEmbed, FakeFactCheck, FakeLLM, FakeSearch


class _OutShape(BaseModel):
    message: str = "ok"
    tries: int = 0


async def test_fake_llm_defaults_to_schema_defaults():
    r = await FakeLLM().complete_json(system="s", user="u", schema=_OutShape)
    assert isinstance(r.parsed, _OutShape)
    assert r.parsed.message == "ok" and r.parsed.tries == 0


async def test_fake_llm_scripter_wins():
    def script(system, user, schema):
        return _OutShape(message="scripted", tries=3)

    r = await FakeLLM(scripter=script).complete_json(system="s", user="u", schema=_OutShape)
    assert r.parsed.message == "scripted" and r.parsed.tries == 3


async def test_fake_embed_is_deterministic_and_correct_dim():
    e = FakeEmbed(dim=16)
    a = await e.embed(["hello", "world"])
    b = await e.embed(["hello", "world"])
    assert a == b
    assert all(len(v) == 16 for v in a)


async def test_fake_search_returns_scripted_results():
    s = FakeSearch(results=[SearchResult(url="https://x", title="t", snippet="s")])
    out = await s.search("anything")
    assert out[0].url == "https://x"


async def test_fake_factcheck_returns_scripted_hits():
    hit = ClaimReviewHit(
        claim_text="c",
        claimant=None,
        claim_date=None,
        review_url="https://r",
        review_publisher="p",
        review_publisher_site=None,
        review_title=None,
        review_date=None,
        textual_rating="False",
    )
    out = await FakeFactCheck(hits=[hit]).search("x")
    assert out[0].textual_rating == "False"
