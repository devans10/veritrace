"""advance_investigation uses the per-case snapshot (not state.INVESTIGATORS).

The invariant from PR review #2: a release that grows ``state.INVESTIGATORS``
mid-pipeline must not strand cases that fanned out under the old list. The
snapshot stored on ``cases.expected_investigators`` is read at advance time.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from provcheck.core.pipeline import state as state_module
from provcheck.core.pipeline.state import advance_investigation


@pytest.fixture(autouse=True)
def _stub_audit(monkeypatch):
    """advance_investigation calls audit.append on the advancing path. The
    real append hashes the prev row and talks to Postgres; we only care
    about the branching logic here, so stub it to a no-op."""
    from provcheck.core.pipeline import state as state_mod

    monkeypatch.setattr(state_mod.audit, "append", lambda *a, **kw: None)


class _Case:
    def __init__(self, status: str, expected: list[str] | None) -> None:
        self.status = status
        self.expected_investigators = expected


class _Run:
    def __init__(self, investigator: str, status: str) -> None:
        self.investigator = investigator
        self.status = status


def _make_session(case: _Case, runs: list[_Run]) -> MagicMock:
    """Build a mock SQLAlchemy session that returns the given rows."""
    session = MagicMock()
    session.get.return_value = case
    query = MagicMock()
    filter_q = MagicMock()
    filter_q.all.return_value = runs
    query.filter.return_value = filter_q
    session.query.return_value = query
    return session


def test_advance_waits_on_the_snapshot_not_the_current_investigators(monkeypatch):
    """Case was fanned out under a two-investigator release. The current
    deploy has five. advance_investigation must advance once the snapshot's
    two runs are terminal — not wait for the three it never created."""
    monkeypatch.setattr(
        state_module,
        "INVESTIGATORS",
        ("claimcheck", "provenance", "source", "corroboration", "media"),
    )
    case = _Case(status="investigating", expected=["claimcheck", "provenance"])
    runs = [_Run("claimcheck", "done"), _Run("provenance", "done")]
    session = _make_session(case, runs)

    result = advance_investigation(session, uuid.uuid4())

    assert result == "scoring"
    assert case.status == "scoring"


def test_advance_waits_when_a_snapshot_investigator_is_still_running(monkeypatch):
    monkeypatch.setattr(state_module, "INVESTIGATORS", ("claimcheck", "provenance"))
    case = _Case(status="investigating", expected=["claimcheck", "provenance"])
    runs = [_Run("claimcheck", "done"), _Run("provenance", "running")]
    session = _make_session(case, runs)

    result = advance_investigation(session, uuid.uuid4())

    assert result is None
    assert case.status == "investigating"


def test_advance_waits_when_a_snapshot_investigator_has_no_run_yet(monkeypatch):
    monkeypatch.setattr(state_module, "INVESTIGATORS", ("claimcheck", "provenance"))
    case = _Case(status="investigating", expected=["claimcheck", "provenance"])
    runs = [_Run("claimcheck", "done")]  # provenance row not created yet
    session = _make_session(case, runs)

    result = advance_investigation(session, uuid.uuid4())

    assert result is None
    assert case.status == "investigating"


def test_null_snapshot_falls_back_to_current_investigators(monkeypatch):
    """Pre-migration case with NULL expected_investigators falls back to
    today's state.INVESTIGATORS, preserving v0.1 behavior."""
    monkeypatch.setattr(state_module, "INVESTIGATORS", ("claimcheck",))
    case = _Case(status="investigating", expected=None)
    runs = [_Run("claimcheck", "done")]
    session = _make_session(case, runs)

    result = advance_investigation(session, uuid.uuid4())

    assert result == "scoring"


def test_extra_runs_beyond_snapshot_do_not_block(monkeypatch):
    """A run for an investigator NOT in the snapshot (e.g. a hand-inserted
    row during debugging) must not block advancement."""
    monkeypatch.setattr(state_module, "INVESTIGATORS", ("claimcheck",))
    case = _Case(status="investigating", expected=["claimcheck"])
    runs = [_Run("claimcheck", "done"), _Run("mystery_investigator", "running")]
    session = _make_session(case, runs)

    result = advance_investigation(session, uuid.uuid4())

    assert result == "scoring"


# touch unused imports so mypy doesn't flag them if a future refactor drops them
_ = (datetime, UTC)
