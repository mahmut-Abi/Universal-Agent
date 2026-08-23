from __future__ import annotations

from datetime import UTC, datetime, timedelta

from universal_agent.core import SessionId, TaskId, immutable_json
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


def test_distributed_runtime_coordinator_schedules_session_work_and_reports_state() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()
    coordinator.workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=30,
        now=now,
    )

    result = coordinator.schedule_session(
        SessionId("session-1"),
        priority=5,
        max_attempts=2,
        available_at=now,
        now=now,
    )

    assert result.scheduled_work_item.kind == "agent_session"
    assert result.scheduled_work_item.session_id == SessionId("session-1")
    assert result.scheduled_work_item.priority == 5
    assert result.scheduled_work_item.max_attempts == 2
    assert result.snapshot.work_queue.queued_count == 1
    assert result.health.status is DistributedHealthStatus.OK


def test_distributed_runtime_coordinator_schedules_goal_work_and_reports_state() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()
    coordinator.workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_goal",),
        ttl_seconds=30,
        now=now,
    )
    payload = immutable_json({"goal": {"description": "verify workload"}})

    result = coordinator.schedule_goal(
        payload=payload,
        idempotency_key="goal:verify-workload",
        priority=6,
        max_attempts=2,
        available_at=now,
        now=now,
    )

    assert result.scheduled_work_item.kind == "agent_goal"
    assert result.scheduled_work_item.session_id is None
    assert result.scheduled_work_item.payload == payload
    assert result.scheduled_work_item.priority == 6
    assert result.scheduled_work_item.max_attempts == 2
    assert result.snapshot.work_queue.queued_count == 1
    assert result.health.status is DistributedHealthStatus.OK


def test_distributed_runtime_coordinator_schedules_task_work_and_reports_state() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()
    coordinator.workers.register(
        WorkerId("worker-a"),
        capabilities=("task",),
        ttl_seconds=30,
        now=now,
    )

    result = coordinator.schedule_task(
        SessionId("session-1"),
        TaskId("task-1"),
        priority=6,
        max_attempts=2,
        available_at=now,
        now=now,
    )

    assert result.scheduled_work_item.kind == "task"
    assert result.scheduled_work_item.session_id == SessionId("session-1")
    assert result.scheduled_work_item.task_id == TaskId("task-1")
    assert result.scheduled_work_item.priority == 6
    assert result.scheduled_work_item.max_attempts == 2
    assert result.snapshot.work_queue.queued_count == 1
    assert result.health.status is DistributedHealthStatus.OK


def test_distributed_runtime_coordinator_manages_worker_lifecycle() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()

    registered = coordinator.register_worker(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=30,
        now=now,
    )
    heartbeat = coordinator.heartbeat_worker(
        WorkerId("worker-a"),
        ttl_seconds=60,
        now=now + timedelta(seconds=5),
    )
    draining = coordinator.drain_worker(
        WorkerId("worker-a"),
        reason="finish current lease",
        now=now + timedelta(seconds=6),
    )
    offline = coordinator.mark_worker_offline(
        WorkerId("worker-a"),
        reason="shutdown complete",
        now=now + timedelta(seconds=7),
    )

    assert registered.worker.status is WorkerStatus.ONLINE
    assert registered.snapshot.workers.online_count == 1
    assert registered.health.status is DistributedHealthStatus.OK
    assert heartbeat.worker.lease_expires_at == now + timedelta(seconds=65)
    assert draining.worker.status is WorkerStatus.DRAINING
    assert draining.worker.last_error == "finish current lease"
    assert draining.snapshot.workers.draining_count == 1
    assert offline.worker.status is WorkerStatus.OFFLINE
    assert offline.snapshot.workers.offline_count == 1


def test_distributed_runtime_coordinator_manages_lock_lifecycle() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()
    coordinator.register_worker(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=30,
        now=now,
    )

    acquired = coordinator.acquire_lock(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=30,
        now=now,
    )
    renewed = coordinator.heartbeat_lock(
        acquired.lock.lease_id,
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=60,
        now=now + timedelta(seconds=5),
    )
    released = coordinator.release_lock(
        renewed.lock.lease_id,
        owner_id=DistributedLockOwnerId("worker-a"),
        now=now + timedelta(seconds=6),
    )

    assert acquired.lock.lock_key == "session/session-1"
    assert acquired.snapshot.locks[0].lock_key == "session/session-1"
    assert renewed.lock.lease_expires_at == now + timedelta(seconds=65)
    assert released.lock.lease_id == acquired.lock.lease_id
    assert released.snapshot.locks == ()


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


def test_distributed_runtime_coordinator_cancels_work_item_and_reports_state() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()
    scheduled = coordinator.scheduler.schedule_session(SessionId("session-1"), available_at=now)
    coordinator.workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=30,
        now=now,
    )

    result = coordinator.cancel_work_item(
        scheduled.work_item_id,
        reason="operator cancelled session work",
        now=now + timedelta(seconds=1),
    )

    assert result.cancelled_work_item.status is WorkItemStatus.CANCELLED
    assert result.cancelled_work_item.last_error == "operator cancelled session work"
    assert result.snapshot.work_queue.queued_count == 0
    assert result.snapshot.work_queue.cancelled_count == 1
    assert result.health.status is DistributedHealthStatus.OK


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
