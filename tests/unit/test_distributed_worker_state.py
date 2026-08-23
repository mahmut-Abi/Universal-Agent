from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from universal_agent.core import immutable_json
from universal_agent.distributed import (
    InMemoryWorkerRegistry,
    WorkerId,
    WorkerNotFoundError,
    WorkerStatus,
)


def test_worker_registry_registers_and_re_registers_worker() -> None:
    registry = InMemoryWorkerRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    first = registry.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        metadata=immutable_json({"host": "local"}),
        ttl_seconds=10,
        now=now,
    )
    second = registry.register(
        WorkerId("worker-a"),
        capabilities=("tool_action",),
        metadata=immutable_json({"host": "local-2"}),
        ttl_seconds=20,
        now=now + timedelta(seconds=1),
    )

    assert first.worker_id == WorkerId("worker-a")
    assert first.status is WorkerStatus.ONLINE
    assert first.registered_at == now
    assert first.lease_expires_at == now + timedelta(seconds=10)
    assert first.metadata["host"] == "local"
    assert second.registered_at == now
    assert second.heartbeat_at == now + timedelta(seconds=1)
    assert second.lease_expires_at == now + timedelta(seconds=21)
    assert second.capabilities == ("tool_action",)
    assert second.metadata["host"] == "local-2"
    assert registry.active() == (second,)


def test_worker_registry_heartbeat_extends_worker_lease() -> None:
    registry = InMemoryWorkerRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    registry.register(WorkerId("worker-a"), ttl_seconds=10, now=now)

    renewed = registry.heartbeat(
        WorkerId("worker-a"),
        ttl_seconds=20,
        now=now + timedelta(seconds=5),
    )

    assert renewed.status is WorkerStatus.ONLINE
    assert renewed.heartbeat_at == now + timedelta(seconds=5)
    assert renewed.lease_expires_at == now + timedelta(seconds=25)
    assert registry.get(WorkerId("worker-a")) == renewed


def test_worker_registry_drains_and_marks_worker_offline() -> None:
    registry = InMemoryWorkerRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    registry.register(WorkerId("worker-a"), now=now)

    draining = registry.drain(
        WorkerId("worker-a"),
        reason="finish current lease only",
        now=now + timedelta(seconds=1),
    )
    offline = registry.mark_offline(
        WorkerId("worker-a"),
        reason="shutdown complete",
        now=now + timedelta(seconds=2),
    )

    assert draining.status is WorkerStatus.DRAINING
    assert draining.last_error == "finish current lease only"
    assert registry.active() == ()
    assert offline.status is WorkerStatus.OFFLINE
    assert offline.last_error == "shutdown complete"
    assert registry.list(status=WorkerStatus.OFFLINE) == (offline,)


def test_worker_registry_expires_online_and_draining_workers() -> None:
    registry = InMemoryWorkerRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    online = registry.register(WorkerId("worker-a"), ttl_seconds=5, now=now)
    draining = registry.register(WorkerId("worker-b"), ttl_seconds=5, now=now)
    registry.drain(WorkerId("worker-b"), now=now + timedelta(seconds=1))
    registry.register(WorkerId("worker-c"), ttl_seconds=5, now=now)
    offline = registry.mark_offline(WorkerId("worker-c"), now=now + timedelta(seconds=1))

    expired = registry.expire(now=now + timedelta(seconds=6))

    assert [record.worker_id for record in expired] == [
        online.worker_id,
        draining.worker_id,
    ]
    assert registry.get(WorkerId("worker-a")).status is WorkerStatus.LOST
    assert registry.get(WorkerId("worker-b")).status is WorkerStatus.LOST
    assert registry.get(WorkerId("worker-c")) == offline
    assert registry.active() == ()


def test_worker_registry_rejects_heartbeat_after_expiry() -> None:
    registry = InMemoryWorkerRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    registry.register(WorkerId("worker-a"), ttl_seconds=5, now=now)

    with pytest.raises(WorkerNotFoundError, match="expired"):
        registry.heartbeat(WorkerId("worker-a"), now=now + timedelta(seconds=6))

    assert registry.get(WorkerId("worker-a")).status is WorkerStatus.LOST


def test_worker_registry_validates_inputs() -> None:
    registry = InMemoryWorkerRegistry()

    with pytest.raises(ValueError, match="worker_id"):
        registry.register(WorkerId(""))
    with pytest.raises(ValueError, match="ttl_seconds"):
        registry.register(WorkerId("worker-a"), ttl_seconds=0)
    with pytest.raises(ValueError, match="drain reason"):
        registry.register(WorkerId("worker-a"))
        registry.drain(WorkerId("worker-a"), reason=" ")
