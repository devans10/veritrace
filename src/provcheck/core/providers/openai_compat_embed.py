"""OpenAI-compatible embeddings adapter.

For hosted/LAN embedding servers exposing ``POST /v1/embeddings`` with the
OpenAI schema (``{model, input, encoding_format}``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from provcheck.core.interfaces import (
    EmbedInputType,
    NotConfigured,
    ProviderError,
    ProviderHealth,
    ProviderUnavailable,
)
from provcheck.core.security.ssrf import trusted_client


class OpenAICompatEmbed:
    name = "openai_compat"

    def __init__(self, *, base_url: str, model: str, dim: int, api_key: str = "") -> None:
        if not base_url:
            raise NotConfigured("EMBED_BASE_URL (or LLM_BASE_URL) is not set")
        if not model:
            raise NotConfigured("EMBED_MODEL is not set")
        self.base_url = base_url.rstrip("/")
        self.model_id = model
        self.dim = dim
        self._key = api_key

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._key:
            h["Authorization"] = f"Bearer {self._key}"
        return h

    async def embed(
        self, texts: Sequence[str], *, input_type: EmbedInputType = "document"
    ) -> list[list[float]]:
        if not texts:
            return []
        body: dict[str, Any] = {"model": self.model_id, "input": list(texts)}
        async with trusted_client(self.base_url, timeout=30) as c:
            try:
                r = await c.post("/v1/embeddings", json=body, headers=self._headers())
            except httpx.TimeoutException as e:
                raise ProviderUnavailable("openai_compat embed timeout") from e
            except httpx.HTTPError as e:
                raise ProviderUnavailable(
                    f"openai_compat embed unreachable: {type(e).__name__}"
                ) from e
        if r.status_code == 401:
            raise NotConfigured("openai_compat embed auth rejected")
        if r.status_code == 429 or r.status_code >= 500:
            raise ProviderUnavailable(f"openai_compat embed HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError as e:
            raise ProviderError(
                f"openai_compat embed returned non-JSON (HTTP {r.status_code})"
            ) from e
        if r.status_code >= 400:
            raise ProviderError(f"openai_compat embed HTTP {r.status_code}: {data}")
        rows = data.get("data") or []
        vecs: list[list[float]] = []
        for row in rows:
            v = row.get("embedding") if isinstance(row, dict) else None
            if not isinstance(v, list):
                raise ProviderError("openai_compat embed: missing embedding")
            if len(v) != self.dim:
                raise ProviderError(
                    f"openai_compat embed returned dim={len(v)}, EMBED_DIM={self.dim}"
                )
            vecs.append([float(x) for x in v])
        return vecs

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, f"model={self.model_id} base={self.base_url}")
