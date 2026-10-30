from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from provcheck.core.interfaces.base import ProviderHealth

JobStatus = Literal["queued", "scheduled", "started", "finished", "failed", "unknown"]


@dataclass(frozen=True)
class JobRef:
    id: str
    queue: str
    task: str


@runtime_checkable
class Queue(Protocol):
    """Background job queue. Synchronous: RQ is sync and the API enqueues from a threadpool.

    ``task`` is a name from the fixed task registry (``provcheck.services.worker.tasks``),
    never an import path taken from data.
    """

    name: str

    def enqueue(
        self,
        task: str,
        payload: Mapping[str, Any],
        *,
        queue: str,
        job_id: str | None = None,
        timeout_s: int = 300,
        retries: int = 0,
        delay_s: int = 0,
    ) -> JobRef: ...

    def status(self, job_id: str) -> JobStatus: ...

    def health(self) -> ProviderHealth: ...
