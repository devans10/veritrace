"""Anthropic Messages API adapter.

Default LLM provider. Uses the ``anthropic`` async SDK against ``/v1/messages``
with ``cache_control: ephemeral`` on the system prompt when ``cache_system=True``
so the long extraction system prompt is only billed once per cache window.
Structured output is produced via ``client.messages.parse()`` with a pydantic
``output_format``.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

from provcheck.core.interfaces import (
    LLMResult,
    LLMUsage,
    NotConfigured,
    ProviderError,
    ProviderHealth,
    ProviderUnavailable,
)

if TYPE_CHECKING:
    import anthropic

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class AnthropicLLM:
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        cache_system: bool = True,
        default_max_tokens: int = 4096,
    ) -> None:
        if not api_key:
            raise NotConfigured("ANTHROPIC_API_KEY is not set")
        try:
            import anthropic
        except ImportError as e:
            raise NotConfigured("anthropic SDK not installed") from e

        self._client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            self._client_kwargs["base_url"] = base_url
        self._client: anthropic.AsyncAnthropic = anthropic.AsyncAnthropic(**self._client_kwargs)
        self.model_id = model
        self._cache_system_default = cache_system
        self._default_max_tokens = default_max_tokens

    def _build_system(self, system: str, *, cache: bool) -> str | list[dict[str, Any]]:
        if not cache:
            return system
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]

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
        import anthropic

        max_tok = max_tokens or self._default_max_tokens
        cache = cache_system or self._cache_system_default
        sys_block = self._build_system(system, cache=cache)
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": max_tok,
            "system": sys_block,
            "messages": [{"role": "user", "content": user}],
            "output_format": schema,
        }
        # temperature is still accepted on Sonnet 4.6; Opus 4.7 would 400 — skip at 0.
        if temperature > 0:
            kwargs["temperature"] = temperature

        client = self._client.with_options(timeout=timeout_s)
        started = time.monotonic()
        try:
            response = await client.messages.parse(**kwargs)
        except anthropic.AuthenticationError as e:
            raise NotConfigured(f"anthropic auth failed: {e}") from e
        except anthropic.PermissionDeniedError as e:
            raise NotConfigured(f"anthropic permission denied: {e}") from e
        except anthropic.BadRequestError as e:
            raise ProviderError(f"anthropic rejected request: {e}") from e
        except (anthropic.RateLimitError, anthropic.APIConnectionError) as e:
            raise ProviderUnavailable(f"anthropic unavailable: {type(e).__name__}") from e
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise ProviderUnavailable(f"anthropic server error: {e.status_code}") from e
            raise ProviderError(f"anthropic error {e.status_code}: {e}") from e
        latency_ms = int((time.monotonic() - started) * 1000)

        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            raise ProviderError("anthropic returned no parsed output")
        if not isinstance(parsed, schema):
            raise ProviderError(f"anthropic parsed output is {type(parsed).__name__}, not schema")

        raw_text = next(
            (getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text"),
            "",
        )
        usage = LLMUsage(
            input_tokens=getattr(response.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(response.usage, "output_tokens", 0) or 0,
            cache_read_input_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(response.usage, "cache_creation_input_tokens", 0)
            or 0,
        )
        return LLMResult(
            parsed=parsed,
            raw_text=raw_text,
            model=response.model,
            usage=usage,
            latency_ms=latency_ms,
            stop_reason=getattr(response, "stop_reason", None),
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, f"model={self.model_id}")
