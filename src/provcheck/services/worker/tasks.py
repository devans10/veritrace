"""Fixed task registry. RQ jobs always call ``dispatch``; payloads only carry data."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from provcheck.core.investigators.claimcheck import run_claimcheck
from provcheck.core.pipeline.capture import run_archive_poll, run_capture
from provcheck.core.pipeline.extract import run_extract
from provcheck.core.pipeline.orchestrator import run_fan_out
from provcheck.core.providers.registry import get_providers


def _capture(p: dict[str, Any]) -> None:
    run_capture(uuid.UUID(p["case_id"]), get_providers())


def _archive_poll(p: dict[str, Any]) -> None:
    run_archive_poll(uuid.UUID(p["case_id"]), uuid.UUID(p["capture_id"]), get_providers())


def _extract(p: dict[str, Any]) -> None:
    run_extract(uuid.UUID(p["case_id"]), get_providers())


def _fan_out(p: dict[str, Any]) -> None:
    run_fan_out(uuid.UUID(p["case_id"]), get_providers())


def _claimcheck(p: dict[str, Any]) -> None:
    run_claimcheck(uuid.UUID(p["case_id"]), uuid.UUID(p["run_id"]), get_providers())


TASKS: dict[str, Callable[[dict[str, Any]], None]] = {
    "pipeline.capture": _capture,
    "pipeline.archive_poll": _archive_poll,
    "pipeline.extract": _extract,
    "pipeline.fan_out": _fan_out,
    "investigator.claimcheck": _claimcheck,
}


def run_task(task: str, payload: dict[str, Any]) -> None:
    try:
        fn = TASKS[task]
    except KeyError:
        raise ValueError(f"unknown task {task!r}") from None
    fn(payload)


def dispatch(task: str, payload: dict[str, Any]) -> None:
    """RQ entrypoint."""
    run_task(task, payload)
