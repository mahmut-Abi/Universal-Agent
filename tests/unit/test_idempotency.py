from __future__ import annotations

from universal_agent.core import SessionId, immutable_json
from universal_agent.runtime.idempotency import (
    IdempotencyStore,
    InMemoryIdempotencyStore,
    compute_idempotency_key,
)


def _session(value: str = "session-1") -> SessionId:
    return SessionId(value)


def test_deterministic_key_for_same_input() -> None:
    args = immutable_json({"name": "x", "count": 3})
    first = compute_idempotency_key(session_id=_session(), capability="deploy", arguments=args)
    second = compute_idempotency_key(session_id=_session(), capability="deploy", arguments=args)
    assert isinstance(first, str)
    assert first == second


def test_different_arguments_produce_different_keys() -> None:
    session_id = _session()
    capability = "deploy"
    a = compute_idempotency_key(
        session_id=session_id, capability=capability, arguments=immutable_json({"a": 1})
    )
    b = compute_idempotency_key(
        session_id=session_id, capability=capability, arguments=immutable_json({"a": 2})
    )
    assert a != b


def test_different_capability_produces_different_key() -> None:
    args = immutable_json({"name": "x"})
    a = compute_idempotency_key(session_id=_session(), capability="deploy", arguments=args)
    b = compute_idempotency_key(session_id=_session(), capability="rollback", arguments=args)
    assert a != b


def test_different_session_produces_different_key() -> None:
    args = immutable_json({"name": "x"})
    a = compute_idempotency_key(session_id=SessionId("s-1"), capability="deploy", arguments=args)
    b = compute_idempotency_key(session_id=SessionId("s-2"), capability="deploy", arguments=args)
    assert a != b


def test_canonical_serialization_order_insensitive() -> None:
    a = compute_idempotency_key(
        session_id=_session(),
        capability="deploy",
        arguments=immutable_json({"b": 2, "a": 1}),
    )
    b = compute_idempotency_key(
        session_id=_session(),
        capability="deploy",
        arguments=immutable_json({"a": 1, "b": 2}),
    )
    assert a == b


def test_store_record_first_true_then_false() -> None:
    store: IdempotencyStore = InMemoryIdempotencyStore()
    key = compute_idempotency_key(
        session_id=_session(), capability="deploy", arguments=immutable_json({"a": 1})
    )
    assert store.seen(key) is False
    assert store.record(key) is True
    assert store.seen(key) is True
    assert store.record(key) is False
    assert store.seen(key) is True


def test_store_clear_resets_seen() -> None:
    store: IdempotencyStore = InMemoryIdempotencyStore()
    key = compute_idempotency_key(
        session_id=_session(), capability="deploy", arguments=immutable_json({"a": 1})
    )
    store.record(key)
    store.clear()
    assert store.seen(key) is False
    assert store.record(key) is True


def test_store_isolated_keys() -> None:
    store: IdempotencyStore = InMemoryIdempotencyStore()
    k1 = compute_idempotency_key(
        session_id=_session(), capability="deploy", arguments=immutable_json({"a": 1})
    )
    k2 = compute_idempotency_key(
        session_id=_session(), capability="deploy", arguments=immutable_json({"a": 2})
    )
    store.record(k1)
    assert store.seen(k2) is False
    assert store.record(k2) is True
