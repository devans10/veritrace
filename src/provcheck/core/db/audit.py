"""Hash-chained audit log.

hash_n = sha256(prev_hash || canonical_json({ts, case_id, actor, event_type, payload}))
A transaction-scoped advisory lock serialises appends so the chain never forks.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from provcheck.core.db.models import AuditLog

GENESIS = "0" * 64
_LOCK_KEY = 0x50524F56  # arbitrary constant ("PROV")


def default_actor() -> str:
    return f"{os.environ.get('SERVICE_NAME', 'provcheck')}@{socket.gethostname()}"


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_hash(
    prev_hash: str,
    *,
    ts: datetime,
    case_id: uuid.UUID | None,
    actor: str,
    event_type: str,
    payload: dict[str, Any],
) -> str:
    body = canonical(
        {
            "ts": ts.astimezone(UTC).isoformat(),
            "case_id": str(case_id) if case_id else None,
            "actor": actor,
            "event_type": event_type,
            "payload": payload,
        }
    )
    return hashlib.sha256((prev_hash + body).encode()).hexdigest()


def append(
    session: Session,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    case_id: uuid.UUID | None = None,
    actor: str | None = None,
) -> AuditLog:
    """Append an audit entry inside the caller's transaction."""
    payload = json.loads(canonical(payload or {}))  # normalise to JSON types up front
    actor = actor or default_actor()
    # Acquire the advisory lock BEFORE any pending inserts flush — otherwise
    # auto-flush would push INSERTs with FK references first, which take SHARE
    # locks on the referenced case row before the advisory lock, inverting the
    # order used by advance_investigation and causing deadlocks under contention.
    with session.no_autoflush:
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
        prev = session.execute(
            select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1)
        ).scalar_one_or_none()
    prev_hash = prev or GENESIS
    # Microsecond precision is what Postgres stores, so the hash survives a round trip.
    ts = datetime.now(UTC)
    entry = AuditLog(
        ts=ts,
        case_id=case_id,
        actor=actor,
        event_type=event_type,
        payload=payload,
        prev_hash=prev_hash,
        hash=compute_hash(
            prev_hash, ts=ts, case_id=case_id, actor=actor, event_type=event_type, payload=payload
        ),
    )
    session.add(entry)
    session.flush()
    return entry


@dataclass(frozen=True)
class ChainCheck:
    ok: bool
    checked: int
    first_bad_id: int | None = None
    reason: str = ""


def verify_entries(
    entries: list[AuditLog], start_prev: str | None = None, *, check_links: bool = True
) -> ChainCheck:
    """Verify entries ordered by id. ``check_links=False`` checks each entry's own hash only
    (for a per-case subset, which is not contiguous in the global chain)."""
    prev = start_prev
    for i, e in enumerate(entries):
        if check_links and prev is not None and e.prev_hash != prev:
            return ChainCheck(False, i, e.id, "prev_hash does not match previous entry")
        expected = compute_hash(
            e.prev_hash,
            ts=e.ts,
            case_id=e.case_id,
            actor=e.actor,
            event_type=e.event_type,
            payload=e.payload,
        )
        if expected != e.hash:
            return ChainCheck(False, i, e.id, "hash mismatch (entry altered)")
        prev = e.hash
    return ChainCheck(True, len(entries))


def verify_chain(session: Session) -> ChainCheck:
    entries = list(session.execute(select(AuditLog).order_by(AuditLog.id)).scalars())
    return verify_entries(entries, start_prev=GENESIS)
