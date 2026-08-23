from __future__ import annotations

from datetime import UTC, datetime, timedelta

from universal_agent.core import SessionId
from universal_agent.distributed import (
    DistributedHealthStatus,
    DistributedLockOwnerId,
    DistributedRuntimeCoordinator,
    InMemoryDistributedLockRegistry,
    InMemoryWorkerRegistry,
    InMemoryWorkQueue,
    WorkerId,
    WorkerStatus,
    WorkItemStatus,
)


def test_distributed_runtime_coordinator_wires_scheduler_snapshot_and_health() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()

    coordinator.scheduler.schedule_session(SessionId("session-1"), available_at=now)
    coordinator.workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=30,
        now=now,
    )

    snapshot = coordinator.snapshot()
    health = coordinator.health(now=now)

    assert snapshot.work_queue.queued_count == 1
    assert snapshot.workers.online_count == 1
    assert health.status is DistributedHealthStatus.OK


def test_distributed_runtime_coordinator_runs_expiry_sweep_with_one_timestamp() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()

    coordinator.queue.enqueue(kind="agent_session", available_at=now)
    leased = coordinator.queue.lease(worker_id=WorkerId("worker-a"), ttl_seconds=5, now=now)
    lock = coordinator.locks.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=5,
        now=now,
    )
    coordinator.workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=5,
        now=now,
    )

    result = coordinator.expire(now=now + timedelta(seconds=6))

    assert result.ran_at == now + timedelta(seconds=6)
    assert [item.work_item_id for item in result.expired_work_items] == [leased.work_item_id]
    assert result.expired_work_items[0].status is WorkItemStatus.QUEUED
    assert result.expired_locks == (lock,)
    assert result.expired_workers[0].worker_id == WorkerId("worker-a")
    assert result.snapshot.work_queue.queued_count == 1
    assert result.snapshot.workers.lost_count == 1
    assert result.health.status is DistributedHealthStatus.ERROR


def test_distributed_runtime_coordinator_accepts_injected_primitives() -> None:
    queue = InMemoryWorkQueue()
    locks = InMemoryDistributedLockRegistry()
    workers = InMemoryWorkerRegistry()
    coordinator = DistributedRuntimeCoordinator(queue=queue, locks=locks, workers=workers)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    scheduled = coordinator.scheduler.schedule_session(SessionId("session-1"), available_at=now)
    workers.register(WorkerId("worker-a"), capabilities=("agent_session",), now=now)

    assert coordinator.queue is queue
    assert coordinator.locks is locks
    assert coordinator.workers is workers
    assert queue.get(scheduled.work_item_id) == scheduled
    assert coordinator.snapshot().workers.workers[0].status is WorkerStatus.ONLINE
