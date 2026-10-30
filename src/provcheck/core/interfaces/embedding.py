from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol, runtime_checkable

from provcheck.core.interfaces.base import ProviderHealth

EmbedInputType = Literal["document", "query"]


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Produces fixed-dimension vector embeddings.

    Split from LLMClient because Anthropic ships no embedding endpoint. The ``dim``
    attribute MUST match the pgvector column size chosen in migration 2.
    """

    name: str
    model_id: str
    dim: int

    async def embed(
        self, texts: Sequence[str], *, input_type: EmbedInputType = "document"
    ) -> list[list[float]]: ...

    async def health(self) -> ProviderHealth: ...
