from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from universal_agent.core import immutable_json
from universal_agent.distributed import (
    DistributedLockConflictError,
    DistributedLockLeaseLostError,
    DistributedLockOwnerId,
    InMemoryDistributedLockRegistry,
)


def test_distributed_lock_registry_acquires_reenters_and_releases() -> None:
    registry = InMemoryDistributedLockRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    first = registry.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=10,
        metadata=immutable_json({"reason": "run session"}),
        now=now,
    )
    second = registry.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=20,
        now=now + timedelta(seconds=1),
    )
    released = registry.release(
        first.lease_id,
        owner_id=DistributedLockOwnerId("worker-a"),
        now=now + timedelta(seconds=2),
    )

    assert first == second
    assert first.lease_expires_at == now + timedelta(seconds=10)
    assert first.metadata["reason"] == "run session"
    assert released == first
    assert registry.active() == ()


def test_distributed_lock_registry_rejects_conflicting_owner() -> None:
    registry = InMemoryDistributedLockRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    registry.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        now=now,
    )

    with pytest.raises(DistributedLockConflictError, match="worker-a"):
        registry.acquire(
            lock_key="session/session-1",
            owner_id=DistributedLockOwnerId("worker-b"),
            now=now,
        )


def test_distributed_lock_registry_heartbeat_extends_lease() -> None:
    registry = InMemoryDistributedLockRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    lease = registry.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=10,
        now=now,
    )

    renewed = registry.heartbeat(
        lease.lease_id,
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=20,
        now=now + timedelta(seconds=5),
    )

    assert renewed.lease_id == lease.lease_id
    assert renewed.heartbeat_at == now + timedelta(seconds=5)
    assert renewed.lease_expires_at == now + timedelta(seconds=25)
    assert registry.active() == (renewed,)


def test_distributed_lock_registry_expires_and_allows_new_owner() -> None:
    registry = InMemoryDistributedLockRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    first = registry.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=5,
        now=now,
    )

    expired = registry.expire(now=now + timedelta(seconds=6))
    second = registry.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-b"),
        ttl_seconds=5,
        now=now + timedelta(seconds=7),
    )

    assert expired == (first,)
    assert second.owner_id == DistributedLockOwnerId("worker-b")
    assert second.lease_id != first.lease_id


def test_distributed_lock_registry_rejects_lost_or_expired_lease_operations() -> None:
    registry = InMemoryDistributedLockRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    lease = registry.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=5,
        now=now,
    )

    with pytest.raises(DistributedLockLeaseLostError, match="another owner"):
        registry.heartbeat(
            lease.lease_id,
            owner_id=DistributedLockOwnerId("worker-b"),
            now=now + timedelta(seconds=1),
        )
    with pytest.raises(DistributedLockLeaseLostError, match="expired"):
        registry.release(
            lease.lease_id,
            owner_id=DistributedLockOwnerId("worker-a"),
            now=now + timedelta(seconds=6),
        )

    assert registry.active() == ()


def test_distributed_lock_registry_validates_inputs() -> None:
    registry = InMemoryDistributedLockRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(ValueError, match="lock_key"):
        registry.acquire(
            lock_key=" ",
            owner_id=DistributedLockOwnerId("worker-a"),
            now=now,
        )
    with pytest.raises(ValueError, match="owner_id"):
        registry.acquire(
            lock_key="session/session-1",
            owner_id=DistributedLockOwnerId(""),
            now=now,
        )
    with pytest.raises(ValueError, match="ttl_seconds"):
        registry.acquire(
            lock_key="session/session-1",
            owner_id=DistributedLockOwnerId("worker-a"),
            ttl_seconds=0,
            now=now,
        )
