"""In-process fakes used by tests and the inline queue.

These implement the same Protocols as the real providers and return deterministic
results so unit and contract tests can run without network, keys, or a model
server.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from typing import TypeVar

from pydantic import BaseModel

from provcheck.core.interfaces import (
    ClaimReviewHit,
    EmbedInputType,
    LLMResult,
    LLMUsage,
    ProviderHealth,
    SearchResult,
    TimeRange,
)

T = TypeVar("T", bound=BaseModel)

ScriptedCompletion = Callable[[str, str, type[BaseModel]], BaseModel]


class FakeLLM:
    """Scripted LLM.

    If no scripter is supplied, ``complete_json`` returns the schema's default
    instance (useful for dev/test stacks that just need the pipeline to progress
    with zero claims). Tests that want specific extraction results register a
    scripter via ``set_scripter``.
    """

    name = "fake"
    model_id = "fake-model"

    def __init__(self, scripter: ScriptedCompletion | None = None) -> None:
        self._scripter = scripter

    def set_scripter(self, fn: ScriptedCompletion) -> None:
        self._scripter = fn

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
        if self._scripter is None:
            try:
                parsed: BaseModel = schema()
            except Exception as e:
                raise RuntimeError(
                    f"FakeLLM: schema {schema.__name__} has no default; call set_scripter() first"
                ) from e
        else:
            parsed = self._scripter(system, user, schema)
            if not isinstance(parsed, schema):
                raise TypeError(f"scripter returned {type(parsed).__name__}, not {schema.__name__}")
        raw = parsed.model_dump_json()
        tokens = max(1, len(raw) // 4)
        return LLMResult(
            parsed=parsed,
            raw_text=raw,
            model=self.model_id,
            usage=LLMUsage(input_tokens=len(user) // 4, output_tokens=tokens),
            latency_ms=0,
            stop_reason="end_turn",
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "fake")


class FakeEmbed:
    """Deterministic embeddings: sha256 of the text folded into `dim` floats in [-1, 1]."""

    name = "fake"
    model_id = "fake-embed"

    def __init__(self, *, dim: int = 32) -> None:
        self.dim = dim

    async def embed(
        self, texts: Sequence[str], *, input_type: EmbedInputType = "document"
    ) -> list[list[float]]:
        out = []
        for text in texts:
            vec = _hash_vector(text, self.dim)
            out.append(vec)
        return out

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, f"fake dim={self.dim}")


class FakeSearch:
    name = "fake"

    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self._results = results or []

    def set_results(self, results: list[SearchResult]) -> None:
        self._results = results

    async def search(
        self,
        query: str,
        *,
        categories: Sequence[str] = ("general",),
        time_range: TimeRange | None = None,
        language: str | None = None,
        site_filter: Sequence[str] | None = None,
        max_results: int = 10,
    ) -> list[SearchResult]:
        return list(self._results[:max_results])

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "fake")


class FakeFactCheck:
    name = "fake"

    def __init__(self, hits: list[ClaimReviewHit] | None = None) -> None:
        self._hits = hits or []

    def set_hits(self, hits: list[ClaimReviewHit]) -> None:
        self._hits = hits

    async def search(
        self,
        claim_text: str,
        *,
        language: str | None = None,
        max_results: int = 10,
    ) -> list[ClaimReviewHit]:
        return list(self._hits[:max_results])

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, "fake")


def _hash_vector(text: str, dim: int) -> list[float]:
    out: list[float] = []
    i = 0
    while len(out) < dim:
        digest = hashlib.sha256(f"{text}|{i}".encode()).digest()
        for b in digest:
            out.append((b / 127.5) - 1.0)
            if len(out) >= dim:
                break
        i += 1
    return out


def claim_payload_from_text(text: str, schema: type[BaseModel]) -> BaseModel:
    """Convenience: scripters often return a hand-written JSON string under test control."""
    return schema.model_validate(json.loads(text))


__all__ = [
    "FakeEmbed",
    "FakeFactCheck",
    "FakeLLM",
    "FakeSearch",
    "ScriptedCompletion",
    "claim_payload_from_text",
]
