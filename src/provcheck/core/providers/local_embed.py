"""Local sentence-transformers embeddings adapter.

Zero-API-key alternative for dev. The model is loaded once per worker, then
encode() runs in a thread pool (sentence-transformers is sync).

The sentence-transformers dependency is behind the optional ``local-embed``
extra; this provider will raise NotConfigured if the extra is not installed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from provcheck.core.interfaces import (
    EmbedInputType,
    NotConfigured,
    ProviderError,
    ProviderHealth,
)

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


class LocalEmbed:
    name = "local"

    def __init__(self, *, model: str, dim: int) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise NotConfigured(
                "sentence-transformers not installed; "
                "install with: uv pip install -e '.[local-embed]'"
            ) from e
        self._st: SentenceTransformer = SentenceTransformer(model)
        actual_dim = self._st.get_sentence_embedding_dimension()
        if actual_dim != dim:
            raise ProviderError(
                f"local embed model {model!r} produces dim={actual_dim}, EMBED_DIM={dim}; "
                "set EMBED_DIM to match or pick a different model"
            )
        self.model_id = model
        self.dim = dim

    async def embed(
        self,
        texts: Sequence[str],
        *,
        input_type: EmbedInputType = "document",
    ) -> list[list[float]]:
        if not texts:
            return []

        def _encode() -> list[list[float]]:
            arr: Any = self._st.encode(
                list(texts), convert_to_numpy=True, normalize_embeddings=True
            )
            return [list(map(float, row)) for row in arr]

        return await asyncio.to_thread(_encode)

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, f"model={self.model_id} dim={self.dim}")
