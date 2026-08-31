from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from universal_agent.core import ActionId, SessionId, TaskId, immutable_json
from universal_agent.distributed import (
    DistributedLockOwnerId,
    InMemoryDistributedLockRegistry,
    InMemoryWorkerRegistry,
    InMemoryWorkQueue,
    WorkerId,
    WorkerStatus,
    WorkItemStatus,
    WorkScheduler,
    build_distributed_runtime_snapshot,
)


@pytest.mark.behavior
def test_distributed_runtime_snapshot_projects_queue_locks_and_workers() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queue = InMemoryWorkQueue()
    scheduler = WorkScheduler(queue)
    locks = InMemoryDistributedLockRegistry()
    workers = InMemoryWorkerRegistry()

    session_work = scheduler.schedule_session(
        SessionId("session-1"),
        payload=immutable_json({"goal": "verify workload health"}),
        priority=1,
        available_at=now,
    )
    action_work = scheduler.schedule_action(
        SessionId("session-1"),
        TaskId("task-1"),
        ActionId("action-1"),
        payload=immutable_json({"capability": "inspect_workload"}),
        priority=10,
        available_at=now,
    )
    leased = queue.lease(worker_id=WorkerId("worker-a"), ttl_seconds=30, now=now)
    locks.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        metadata=immutable_json({"purpose": "session execution"}),
        ttl_seconds=30,
        now=now,
    )
    workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_session", "tool_action"),
        ttl_seconds=30,
        now=now,
    )
    workers.register(WorkerId("worker-b"), ttl_seconds=30, now=now)
    workers.drain(WorkerId("worker-b"), now=now + timedelta(seconds=1))
    workers.register(WorkerId("worker-c"), ttl_seconds=30, now=now)
    workers.mark_offline(WorkerId("worker-c"), now=now + timedelta(seconds=1))
    workers.register(WorkerId("worker-d"), ttl_seconds=5, now=now)
    workers.expire(now=now + timedelta(seconds=6))

    snapshot = build_distributed_runtime_snapshot(queue=queue, locks=locks, workers=workers)

    assert snapshot.work_queue.total_count == 2
    assert snapshot.work_queue.queued_count == 1
    assert snapshot.work_queue.leased_count == 1
    assert snapshot.work_queue.completed_count == 0
    assert [item.work_item_id for item in snapshot.work_queue.items] == [
        action_work.work_item_id,
        session_work.work_item_id,
    ]
    leased_item = snapshot.work_queue.items[0]
    assert leased_item.status is WorkItemStatus.LEASED
    assert leased_item.worker_id == WorkerId("worker-a")
    assert leased_item.lease_expires_at == now + timedelta(seconds=30)
    assert leased_item.fencing_token == 1
    assert leased.work_item_id == action_work.work_item_id
    assert snapshot.locks[0].lock_key == "session/session-1"
    assert snapshot.locks[0].owner_id == DistributedLockOwnerId("worker-a")
    assert snapshot.locks[0].fencing_token == 1
    assert snapshot.locks[0].metadata["purpose"] == "session execution"
    assert snapshot.workers.total_count == 4
    assert snapshot.workers.online_count == 1
    assert snapshot.workers.draining_count == 1
    assert snapshot.workers.offline_count == 1
    assert snapshot.workers.lost_count == 1
    assert [worker.status for worker in snapshot.workers.workers] == [
        WorkerStatus.ONLINE,
        WorkerStatus.DRAINING,
        WorkerStatus.OFFLINE,
        WorkerStatus.LOST,
    ]


@pytest.mark.unit
def test_distributed_runtime_snapshot_is_read_only() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queue = InMemoryWorkQueue()
    queue.enqueue(kind="agent_session", available_at=now)
    leased = queue.lease(worker_id=WorkerId("worker-a"), ttl_seconds=1, now=now)
    locks = InMemoryDistributedLockRegistry()
    lock = locks.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=1,
        now=now,
    )
    workers = InMemoryWorkerRegistry()
    workers.register(WorkerId("worker-a"), ttl_seconds=1, now=now)

    snapshot = build_distributed_runtime_snapshot(queue=queue, locks=locks, workers=workers)

    assert snapshot.work_queue.leased_count == 1
    assert queue.get(leased.work_item_id).status is WorkItemStatus.LEASED
    assert locks.active() == (lock,)
    assert workers.get(WorkerId("worker-a")).status is WorkerStatus.ONLINE


@pytest.mark.unit
def test_distributed_runtime_snapshot_allows_missing_optional_registries() -> None:
    queue = InMemoryWorkQueue()
    queue.enqueue(kind="agent_session")

    snapshot = build_distributed_runtime_snapshot(queue=queue)

    assert snapshot.work_queue.total_count == 1
    assert snapshot.locks == ()
    assert snapshot.workers.total_count == 0
    assert snapshot.workers.workers == ()
