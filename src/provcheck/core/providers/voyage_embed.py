"""Voyage AI embeddings adapter (Anthropic's recommended embedding partner).

Uses the ``voyageai`` async SDK. Model id and dim are declared by the caller so
they match the pgvector column size chosen in migration 2.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from provcheck.core.interfaces import (
    EmbedInputType,
    NotConfigured,
    ProviderError,
    ProviderHealth,
    ProviderUnavailable,
)

log = logging.getLogger(__name__)


class VoyageEmbed:
    name = "voyage"

    def __init__(self, *, api_key: str, model: str, dim: int) -> None:
        if not api_key:
            raise NotConfigured("VOYAGE_API_KEY is not set")
        try:
            import voyageai
        except ImportError as e:
            raise NotConfigured("voyageai SDK not installed") from e
        self._client: Any = voyageai.AsyncClient(api_key=api_key)  # type: ignore[attr-defined]
        self.model_id = model
        self.dim = dim

    async def embed(
        self, texts: Sequence[str], *, input_type: EmbedInputType = "document"
    ) -> list[list[float]]:
        if not texts:
            return []
        try:
            result: Any = await self._client.embed(
                list(texts), model=self.model_id, input_type=input_type
            )
        except Exception as e:
            name = type(e).__name__
            msg = str(e).lower()
            if (
                "auth" in msg
                or "api key" in msg
                or name
                in {
                    "AuthenticationError",
                    "InvalidRequestError",
                }
            ):
                raise NotConfigured(f"voyage auth: {e}") from e
            if "rate" in msg or "timeout" in msg or "unavailable" in msg:
                raise ProviderUnavailable(f"voyage unavailable: {name}") from e
            raise ProviderError(f"voyage error: {name}: {e}") from e
        vecs = getattr(result, "embeddings", None)
        if not vecs:
            raise ProviderError("voyage returned no embeddings")
        for v in vecs:
            if len(v) != self.dim:
                raise ProviderError(
                    f"voyage returned dim={len(v)}, EMBED_DIM={self.dim}; "
                    "pgvector column size will not match"
                )
        return list(vecs)

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, f"model={self.model_id} dim={self.dim}")
