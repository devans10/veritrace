import uuid
from datetime import UTC, datetime

from provcheck.core.db.audit import GENESIS, compute_hash, verify_entries
from provcheck.core.db.models import AuditLog


def _chain(n=3):
    entries, prev = [], GENESIS
    case = uuid.uuid4()
    for i in range(n):
        ts = datetime(2026, 10, 26, 12, 0, i, 123456, tzinfo=UTC)
        payload = {"i": i, "nested": {"b": 2, "a": 1}}
        h = compute_hash(prev, ts=ts, case_id=case, actor="t", event_type="e", payload=payload)
        entries.append(
            AuditLog(
                id=i + 1,
                ts=ts,
                case_id=case,
                actor="t",
                event_type="e",
                payload=payload,
                prev_hash=prev,
                hash=h,
            )
        )
        prev = h
    return entries


def test_hash_is_deterministic_and_key_order_independent():
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    a = compute_hash(
        GENESIS, ts=ts, case_id=None, actor="x", event_type="y", payload={"a": 1, "b": 2}
    )
    b = compute_hash(
        GENESIS, ts=ts, case_id=None, actor="x", event_type="y", payload={"b": 2, "a": 1}
    )
    assert a == b and len(a) == 64


def test_valid_chain_verifies():
    assert verify_entries(_chain(), start_prev=GENESIS).ok


def test_tampered_payload_detected():
    entries = _chain()
    entries[1].payload = {"i": 99}
    res = verify_entries(entries, start_prev=GENESIS)
    assert not res.ok and res.first_bad_id == 2 and "altered" in res.reason


def test_deleted_entry_detected():
    entries = _chain(4)
    del entries[2]
    res = verify_entries(entries, start_prev=GENESIS)
    assert not res.ok and res.first_bad_id == 4


def test_subset_check_without_links():
    entries = _chain(4)
    assert verify_entries([entries[0], entries[3]], check_links=False).ok
