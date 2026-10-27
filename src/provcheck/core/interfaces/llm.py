from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from provcheck.core.interfaces.base import ProviderHealth

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(frozen=True)
class LLMResult:
    """Result of a structured-output completion. ``parsed`` is a validated pydantic model."""

    parsed: BaseModel
    raw_text: str
    model: str
    usage: LLMUsage
    latency_ms: int
    stop_reason: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLMClient(Protocol):
    """Structured-output completion against a chat LLM.

    ``complete_json`` returns a pydantic instance validated against ``schema``.
    Implementations SHOULD support prompt caching when ``cache_system=True`` so the
    long extraction system prompt is only billed once per cache window.
    """

    name: str
    model_id: str

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
    ) -> LLMResult: ...

    async def health(self) -> ProviderHealth: ...
