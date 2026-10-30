"""OpenAI-compatible LLM adapter: structured output + JSON-mode fallback + repair retry."""

import json

import httpx
import pytest
import respx
from pydantic import BaseModel

from provcheck.core.interfaces import NotConfigured, ProviderError, ProviderUnavailable
from provcheck.core.providers.openai_compat_llm import OpenAICompatLLM

BASE = "http://llm.local:8080"


class Shape(BaseModel):
    name: str
    sides: int


def _chat_body(content: str) -> dict:
    return {
        "model": "fake-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 42, "completion_tokens": 7},
    }


@respx.mock
async def test_structured_output_happy_path():
    respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_chat_body('{"name":"square","sides":4}'))
    )
    llm = OpenAICompatLLM(base_url=BASE, model="fake-model")
    r = await llm.complete_json(system="S", user="U", schema=Shape)
    assert isinstance(r.parsed, Shape)
    assert r.parsed.name == "square" and r.parsed.sides == 4
    assert r.usage.input_tokens == 42
    assert r.usage.output_tokens == 7


@respx.mock
async def test_falls_back_to_json_object_when_schema_rejected():
    # First call: 400 "invalid parameter: response_format.json_schema"
    # Second call: success with json_object
    route = respx.post(f"{BASE}/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(400, json={"error": {"message": "unknown field: json_schema"}}),
            httpx.Response(200, json=_chat_body('{"name":"triangle","sides":3}')),
        ]
    )
    llm = OpenAICompatLLM(base_url=BASE, model="fake-model")
    r = await llm.complete_json(system="S", user="U", schema=Shape)
    assert r.parsed.sides == 3
    # Second request body should use json_object, not json_schema.
    second = json.loads(route.calls[1].request.content)
    assert second["response_format"] == {"type": "json_object"}


@respx.mock
async def test_repair_retry_on_invalid_json():
    route = respx.post(f"{BASE}/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=_chat_body('{"name":"square"')),  # invalid JSON
            httpx.Response(200, json=_chat_body('{"name":"square","sides":4}')),
        ]
    )
    llm = OpenAICompatLLM(base_url=BASE, model="fake-model")
    r = await llm.complete_json(system="S", user="U", schema=Shape)
    assert r.parsed.sides == 4
    assert route.call_count == 2


@respx.mock
async def test_auth_rejected_is_not_configured():
    respx.post(f"{BASE}/v1/chat/completions").mock(return_value=httpx.Response(401))
    with pytest.raises(NotConfigured):
        await OpenAICompatLLM(base_url=BASE, model="fake-model").complete_json(
            system="S", user="U", schema=Shape
        )


@respx.mock
async def test_rate_limit_is_unavailable():
    respx.post(f"{BASE}/v1/chat/completions").mock(return_value=httpx.Response(429))
    with pytest.raises(ProviderUnavailable):
        await OpenAICompatLLM(base_url=BASE, model="fake-model").complete_json(
            system="S", user="U", schema=Shape
        )


@respx.mock
async def test_server_error_is_unavailable():
    respx.post(f"{BASE}/v1/chat/completions").mock(return_value=httpx.Response(503))
    with pytest.raises(ProviderUnavailable):
        await OpenAICompatLLM(base_url=BASE, model="fake-model").complete_json(
            system="S", user="U", schema=Shape
        )


@respx.mock
async def test_bogus_json_body_from_server_is_provider_error():
    respx.post(f"{BASE}/v1/chat/completions").mock(
        return_value=httpx.Response(200, text="not json")
    )
    with pytest.raises(ProviderError):
        await OpenAICompatLLM(base_url=BASE, model="fake-model").complete_json(
            system="S", user="U", schema=Shape
        )


def test_misconfigured_llm_raises():
    with pytest.raises(NotConfigured):
        OpenAICompatLLM(base_url="", model="x")
    with pytest.raises(NotConfigured):
        OpenAICompatLLM(base_url=BASE, model="")
