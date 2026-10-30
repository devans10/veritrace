"""OpenAI-compatible LLM adapter (alternative to Anthropic).

For self-hosted or LAN endpoints (vLLM, LM Studio, Ollama, LiteLLM proxy).
Structured output tries ``response_format=json_schema`` first; falls back to
``json_object`` + pydantic validation + one repair retry when the server
rejects schema mode.

Routes through :func:`trusted_client` so an internal / private-IP LAN endpoint
is explicitly allowed by SSRF policy.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from provcheck.core.interfaces import (
    LLMResult,
    LLMUsage,
    NotConfigured,
    ProviderError,
    ProviderHealth,
    ProviderUnavailable,
)
from provcheck.core.security.ssrf import trusted_client

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class OpenAICompatLLM:
    name = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        default_max_tokens: int = 4096,
    ) -> None:
        if not base_url:
            raise NotConfigured("LLM_BASE_URL is not set")
        if not model:
            raise NotConfigured("LLM_MODEL is not set")
        self.base_url = base_url.rstrip("/")
        self.model_id = model
        self._api_key = api_key
        self._default_max_tokens = default_max_tokens

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    async def _post(self, body: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        async with trusted_client(self.base_url, timeout=timeout_s) as c:
            try:
                r = await c.post("/v1/chat/completions", json=body, headers=self._headers())
            except httpx.TimeoutException as e:
                raise ProviderUnavailable("openai_compat timeout") from e
            except httpx.HTTPError as e:
                raise ProviderUnavailable(f"openai_compat unreachable: {type(e).__name__}") from e
        if r.status_code == 401:
            raise NotConfigured("openai_compat auth rejected")
        if r.status_code == 429 or r.status_code >= 500:
            raise ProviderUnavailable(f"openai_compat HTTP {r.status_code}")
        try:
            data: dict[str, Any] = r.json()
        except ValueError as e:
            raise ProviderError(f"openai_compat returned non-JSON (HTTP {r.status_code})") from e
        if r.status_code >= 400:
            raise ProviderError(f"openai_compat HTTP {r.status_code}: {data}")
        return data

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_s: float = 120,
        cache_system: bool = False,
    ) -> LLMResult:
        max_tok = max_tokens or self._default_max_tokens
        base: dict[str, Any] = {
            "model": self.model_id,
            "temperature": temperature,
            "max_tokens": max_tok,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        schema_body = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": schema.model_json_schema(),
                "strict": True,
            },
        }
        attempt = dict(base)
        attempt["response_format"] = schema_body

        started = time.monotonic()
        try:
            data = await self._post(attempt, timeout_s=timeout_s)
        except ProviderError as e:
            # Server rejected json_schema; retry with json_object + repair.
            if "json_schema" not in str(e):
                raise
            log.info("openai_compat %s: falling back to json_object mode", e)
            fallback = dict(base)
            fallback["response_format"] = {"type": "json_object"}
            data = await self._post(fallback, timeout_s=timeout_s)
        latency_ms = int((time.monotonic() - started) * 1000)

        text = _extract_text(data)
        try:
            parsed = schema.model_validate_json(text)
        except ValidationError:
            # One repair attempt: ask the model to fix invalid JSON.
            repair_msgs = base["messages"] + [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": (
                        "That response did not match the required JSON schema. "
                        f"Return JSON matching: {json.dumps(schema.model_json_schema())}. "
                        "Return ONLY the JSON, no prose."
                    ),
                },
            ]
            repair = dict(base, messages=repair_msgs, response_format={"type": "json_object"})
            data = await self._post(repair, timeout_s=timeout_s)
            text = _extract_text(data)
            try:
                parsed = schema.model_validate_json(text)
            except ValidationError as ve:
                raise ProviderError(
                    f"openai_compat produced invalid JSON after repair: {ve}"
                ) from ve

        usage_raw = data.get("usage") or {}
        usage = LLMUsage(
            input_tokens=int(usage_raw.get("prompt_tokens") or 0),
            output_tokens=int(usage_raw.get("completion_tokens") or 0),
        )
        return LLMResult(
            parsed=parsed,
            raw_text=text,
            model=str(data.get("model") or self.model_id),
            usage=usage,
            latency_ms=latency_ms,
            stop_reason=_first_finish_reason(data),
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, f"model={self.model_id} base={self.base_url}")


def _extract_text(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise ProviderError("openai_compat response has no choices")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content if isinstance(c, dict)]
        return "".join(parts)
    raise ProviderError("openai_compat response choice has no text content")


def _first_finish_reason(data: dict[str, Any]) -> str | None:
    choices = data.get("choices") or []
    if choices and isinstance(choices[0], dict):
        return choices[0].get("finish_reason")
    return None
