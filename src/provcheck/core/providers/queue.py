"""Queue implementations: RQ (Redis) and an inline queue for tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

from redis import Redis
from rq import Queue as RQQueue
from rq import Retry
from rq.exceptions import NoSuchJobError
from rq.job import Job

from provcheck.core.interfaces import JobRef, JobStatus, ProviderHealth

# Every RQ job calls this single entrypoint, which dispatches through the fixed
# task registry. Job payloads can never name an arbitrary function to import.
DISPATCH = "provcheck.services.worker.tasks.dispatch"

_RQ_STATUS: dict[str, JobStatus] = {
    "queued": "queued",
    "deferred": "queued",
    "scheduled": "scheduled",
    "started": "started",
    "finished": "finished",
    "failed": "failed",
    "stopped": "failed",
    "canceled": "failed",
}


class RQJobQueue:
    name = "rq"

    def __init__(self, redis_url: str) -> None:
        self.redis = Redis.from_url(redis_url)

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
    ) -> JobRef:
        q = RQQueue(queue, connection=self.redis)
        kwargs: dict[str, Any] = {
            "args": (task, dict(payload)),
            "job_id": job_id,
            "job_timeout": timeout_s,
            "result_ttl": 24 * 3600,
            "failure_ttl": 7 * 24 * 3600,
            "description": task,
        }
        if retries:
            kwargs["retry"] = Retry(max=retries, interval=[10, 30, 90][:retries])
        if delay_s > 0:
            job = q.enqueue_in(timedelta(seconds=delay_s), DISPATCH, **kwargs)
        else:
            job = q.enqueue(DISPATCH, **kwargs)
        return JobRef(id=job.id, queue=queue, task=task)

    def status(self, job_id: str) -> JobStatus:
        try:
            job = Job.fetch(job_id, connection=self.redis)
        except NoSuchJobError:
            return "unknown"
        return _RQ_STATUS.get(str(job.get_status(refresh=True)), "unknown")

    def health(self) -> ProviderHealth:
        try:
            ok = bool(self.redis.ping())
        except Exception as e:
            return ProviderHealth(self.name, False, f"{type(e).__name__}: {e}")
        return ProviderHealth(self.name, ok)


class InlineQueue:
    """Runs jobs synchronously in-process (tests). Delayed jobs are recorded, not run."""

    name = "inline"

    def __init__(self, runner: Callable[[str, dict[str, Any]], Any] | None = None) -> None:
        self.runner = runner
        self.jobs: list[tuple[str, dict[str, Any], str, int]] = []
        self.delayed: list[tuple[str, dict[str, Any], str, int]] = []
        self._status: dict[str, JobStatus] = {}
        self._n = 0

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
    ) -> JobRef:
        self._n += 1
        jid = job_id or f"inline-{self._n}"
        record = (task, dict(payload), queue, delay_s)
        if delay_s > 0 or self.runner is None:
            (self.delayed if delay_s > 0 else self.jobs).append(record)
            self._status[jid] = "scheduled" if delay_s > 0 else "queued"
            return JobRef(id=jid, queue=queue, task=task)
        self.jobs.append(record)
        try:
            self.runner(task, dict(payload))
            self._status[jid] = "finished"
        except Exception:
            self._status[jid] = "failed"
            raise
        return JobRef(id=jid, queue=queue, task=task)

    def status(self, job_id: str) -> JobStatus:
        return self._status.get(job_id, "unknown")

    def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, True)
