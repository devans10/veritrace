from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol, runtime_checkable

from provcheck.core.interfaces.base import ProviderHealth


@dataclass(frozen=True)
class StoredObject:
    key: str
    sha256: str
    size: int
    content_type: str


@runtime_checkable
class ObjectStore(Protocol):
    """Content-addressed blob storage. Keys are ``{prefix}/sha256/{hex[:2]}/{hex}``."""

    name: str

    async def put(
        self,
        data: bytes,
        *,
        content_type: str,
        prefix: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StoredObject: ...

    async def get(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...

    async def presigned_get_url(self, key: str, expires: timedelta) -> str: ...

    async def delete(self, key: str) -> None: ...

    async def health(self) -> ProviderHealth: ...
