from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from provcheck.core.interfaces.base import ProviderHealth

ArchiveJobState = Literal["pending", "success", "error"]


@dataclass(frozen=True)
class ArchiveJob:
    job_id: str
    url: str


@dataclass(frozen=True)
class ArchiveJobStatus:
    job_id: str
    state: ArchiveJobState
    archive_url: str | None = None
    timestamp: str | None = None
    error_code: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class ArchiveCapture:
    url: str
    archive_url: str
    captured_at: datetime
    status_code: int | None
    digest: str | None


@runtime_checkable
class ArchiveProvider(Protocol):
    """Public web archive. Saving publishes the URL; callers must respect opt-out."""

    name: str

    async def save(self, url: str) -> ArchiveJob: ...

    async def status(self, job: ArchiveJob) -> ArchiveJobStatus: ...

    async def history(self, url: str, *, limit: int = 50) -> list[ArchiveCapture]: ...

    async def health(self) -> ProviderHealth: ...
