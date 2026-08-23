from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from universal_agent.core import ActionId, SessionId, TaskId, immutable_json, runtime_primitives
from universal_agent.distributed import (
    InMemoryWorkerRegistry,
    InMemoryWorkQueue,
    LeaseLostError,
    NoWorkAvailable,
    WorkerId,
    WorkerRunStatus,
    WorkerStatus,
    WorkHandlerResult,
    WorkItem,
    WorkItemId,
    WorkItemStatus,
    WorkQueueWorker,
)


def test_work_queue_leases_highest_priority_available_item_and_completes_it() -> None:
    queue = InMemoryWorkQueue()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    low = queue.enqueue(
        kind="agent_session",
        priority=1,
        available_at=now,
        work_item_id=WorkItemId("work-low"),
    )
    high = queue.enqueue(
        kind="tool_action",
        payload=immutable_json({"capability": "inspect_workload"}),
        session_id=SessionId("session-1"),
        task_id=TaskId("task-1"),
        action_id=ActionId("action-1"),
        priority=10,
        available_at=now,
        work_item_id=WorkItemId("work-high"),
    )

    leased = queue.lease(worker_id=WorkerId("worker-a"), ttl_seconds=10, now=now)
    assert leased.lease is not None
    completed = queue.complete(
        leased.lease.lease_id,
        worker_id=WorkerId("worker-a"),
        now=now + timedelta(seconds=1),
    )

    assert leased.work_item_id == high.work_item_id
    assert leased.attempts == 1
    assert leased.lease is not None
    assert leased.lease.lease_expires_at == now + timedelta(seconds=10)
    assert completed.status is WorkItemStatus.COMPLETED
    assert completed.completed_at == now + timedelta(seconds=1)
    assert queue.get(low.work_item_id).status is WorkItemStatus.QUEUED


def test_work_queue_idempotent_enqueue_returns_existing_item() -> None:
    queue = InMemoryWorkQueue()

    first = queue.enqueue(kind="agent_session", idempotency_key="session-1:goal-1")
    second = queue.enqueue(kind="agent_session", idempotency_key="session-1:goal-1")

    assert first == second
    assert len(queue.queued()) == 1


def test_work_queue_heartbeat_extends_active_lease_and_rejects_wrong_worker() -> None:
    queue = InMemoryWorkQueue()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queue.enqueue(kind="agent_session", available_at=now)
    leased = queue.lease(worker_id=WorkerId("worker-a"), ttl_seconds=10, now=now)
    assert leased.lease is not None

    renewed = queue.heartbeat(
        leased.lease.lease_id,
        worker_id=WorkerId("worker-a"),
        ttl_seconds=20,
        now=now + timedelta(seconds=5),
    )

    assert renewed.lease is not None
    assert renewed.lease.heartbeat_at == now + timedelta(seconds=5)
    assert renewed.lease.lease_expires_at == now + timedelta(seconds=25)
    with pytest.raises(LeaseLostError, match="another worker"):
        queue.heartbeat(
            renewed.lease.lease_id,
            worker_id=WorkerId("worker-b"),
            now=now + timedelta(seconds=6),
        )


def test_work_queue_fail_requeues_until_max_attempts_then_fails() -> None:
    queue = InMemoryWorkQueue()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    item = queue.enqueue(kind="agent_session", max_attempts=2, available_at=now)

    first = queue.lease(worker_id=WorkerId("worker-a"), now=now)
    assert first.lease is not None
    retry = queue.fail(
        first.lease.lease_id,
        worker_id=WorkerId("worker-a"),
        reason="transient",
        retry=True,
        now=now + timedelta(seconds=1),
    )
    second = queue.lease(worker_id=WorkerId("worker-b"), now=now + timedelta(seconds=2))
    assert second.lease is not None
    terminal = queue.fail(
        second.lease.lease_id,
        worker_id=WorkerId("worker-b"),
        reason="still failing",
        retry=True,
        now=now + timedelta(seconds=3),
    )

    assert retry.status is WorkItemStatus.QUEUED
    assert retry.attempts == 1
    assert terminal.work_item_id == item.work_item_id
    assert terminal.status is WorkItemStatus.FAILED
    assert terminal.attempts == 2
    assert terminal.last_error == "still failing"


def test_work_queue_expire_requeues_or_fails_expired_leases() -> None:
    queue = InMemoryWorkQueue()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queue.enqueue(kind="retryable", max_attempts=2, available_at=now)
    queue.enqueue(kind="terminal", max_attempts=1, available_at=now)

    first = queue.lease(worker_id=WorkerId("worker-a"), ttl_seconds=5, now=now)
    second = queue.lease(worker_id=WorkerId("worker-b"), ttl_seconds=5, now=now)
    expired = queue.expire(now=now + timedelta(seconds=6))

    statuses = {item.kind: item.status for item in expired}
    assert first.status is WorkItemStatus.LEASED
    assert second.status is WorkItemStatus.LEASED
    assert statuses == {
        "retryable": WorkItemStatus.QUEUED,
        "terminal": WorkItemStatus.FAILED,
    }
    assert len(queue.leased()) == 0


def test_work_queue_cancel_removes_pending_or_leased_work_from_execution() -> None:
    queue = InMemoryWorkQueue()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queued = queue.enqueue(kind="queued", priority=0, available_at=now)
    queue.enqueue(kind="leased", priority=1, available_at=now)
    leased = queue.lease(worker_id=WorkerId("worker-a"), now=now)

    cancelled_queued = queue.cancel(queued.work_item_id, reason="session cancelled", now=now)
    cancelled_leased = queue.cancel(leased.work_item_id, reason="session cancelled", now=now)

    assert cancelled_queued.status is WorkItemStatus.CANCELLED
    assert cancelled_leased.status is WorkItemStatus.CANCELLED
    with pytest.raises(NoWorkAvailable):
        queue.lease(worker_id=WorkerId("worker-b"), now=now)


def test_work_queue_rejects_completion_after_lease_expiry() -> None:
    queue = InMemoryWorkQueue()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queue.enqueue(kind="agent_session", max_attempts=2, available_at=now)
    leased = queue.lease(worker_id=WorkerId("worker-a"), ttl_seconds=5, now=now)
    assert leased.lease is not None

    with pytest.raises(LeaseLostError, match="lease expired"):
        queue.complete(
            leased.lease.lease_id,
            worker_id=WorkerId("worker-a"),
            now=now + timedelta(seconds=6),
        )

    item = queue.get(leased.work_item_id)
    assert item.status is WorkItemStatus.QUEUED
    assert item.lease is None
    assert item.last_error is not None
    assert "lease expired" in item.last_error


def test_work_queue_validates_inputs() -> None:
    queue = InMemoryWorkQueue()

    with pytest.raises(ValueError, match="kind"):
        queue.enqueue(kind=" ")
    with pytest.raises(ValueError, match="max_attempts"):
        queue.enqueue(kind="agent_session", max_attempts=0)
    queue.enqueue(kind="agent_session")
    with pytest.raises(ValueError, match="ttl_seconds"):
        queue.lease(worker_id=WorkerId("worker-a"), ttl_seconds=0)


@pytest.mark.asyncio
async def test_work_queue_worker_completes_handled_work() -> None:
    queue = InMemoryWorkQueue()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queue.enqueue(
        kind="agent_session",
        payload=immutable_json({"goal": "inspect"}),
        available_at=now,
    )

    worker = WorkQueueWorker(
        queue=queue,
        worker_id=WorkerId("worker-a"),
        handlers={
            "agent_session": lambda item: WorkHandlerResult.completed(
                f"handled {item.kind}"
            )
        },
    )
    result = await worker.run_once()

    assert result.status is WorkerRunStatus.COMPLETED
    assert result.work_item is not None
    assert result.work_item.status is WorkItemStatus.COMPLETED
    assert result.reason == "handled agent_session"


@pytest.mark.asyncio
async def test_work_queue_worker_rejects_completion_after_lease_expiry() -> None:
    queue = InMemoryWorkQueue()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    current_time = now

    def clock() -> datetime:
        return current_time

    def handler(item: WorkItem) -> WorkHandlerResult:
        nonlocal current_time
        current_time = now + timedelta(seconds=6)
        return WorkHandlerResult.completed("late success")

    queue.enqueue(kind="agent_session", max_attempts=2, available_at=now)
    worker = WorkQueueWorker(
        queue=queue,
        worker_id=WorkerId("worker-a"),
        handlers={"agent_session": handler},
        lease_ttl_seconds=5,
    )

    with runtime_primitives(clock=clock):
        result = await worker.run_once()

    assert result.status is WorkerRunStatus.LEASE_LOST
    assert result.work_item is not None
    assert result.work_item.status is WorkItemStatus.QUEUED
    assert result.reason.startswith("lease expired:")


@pytest.mark.asyncio
async def test_work_queue_worker_retries_handler_failures_until_terminal() -> None:
    queue = InMemoryWorkQueue()
    queue.enqueue(kind="agent_session", max_attempts=2)
    worker = WorkQueueWorker(
        queue=queue,
        worker_id=WorkerId("worker-a"),
        handlers={"agent_session": lambda item: WorkHandlerResult.failed("transient")},
    )

    retry = await worker.run_once()
    failed = await worker.run_once()

    assert retry.status is WorkerRunStatus.RETRYING
    assert retry.work_item is not None
    assert retry.work_item.status is WorkItemStatus.QUEUED
    assert failed.status is WorkerRunStatus.FAILED
    assert failed.work_item is not None
    assert failed.work_item.status is WorkItemStatus.FAILED
    assert failed.work_item.attempts == 2


@pytest.mark.asyncio
async def test_work_queue_worker_fails_unhandled_work_without_retry_loop() -> None:
    queue = InMemoryWorkQueue()
    queue.enqueue(kind="missing_handler")
    worker = WorkQueueWorker(queue=queue, worker_id=WorkerId("worker-a"), handlers={})

    result = await worker.run_once()

    assert result.status is WorkerRunStatus.FAILED
    assert result.work_item is not None
    assert result.work_item.status is WorkItemStatus.FAILED
    assert result.work_item.attempts == 1
    assert result.reason == "no handler registered for work kind: missing_handler"


@pytest.mark.asyncio
async def test_work_queue_worker_can_cancel_work() -> None:
    queue = InMemoryWorkQueue()
    queue.enqueue(kind="agent_session")
    worker = WorkQueueWorker(
        queue=queue,
        worker_id=WorkerId("worker-a"),
        handlers={"agent_session": lambda item: WorkHandlerResult.cancelled("session cancelled")},
    )

    result = await worker.run_once()

    assert result.status is WorkerRunStatus.CANCELLED
    assert result.work_item is not None
    assert result.work_item.status is WorkItemStatus.CANCELLED
    assert result.reason == "session cancelled"


@pytest.mark.asyncio
async def test_work_queue_worker_runs_until_idle() -> None:
    queue = InMemoryWorkQueue()
    queue.enqueue(kind="agent_session")
    queue.enqueue(kind="agent_session")
    worker = WorkQueueWorker(
        queue=queue,
        worker_id=WorkerId("worker-a"),
        handlers={"agent_session": lambda item: WorkHandlerResult.completed()},
    )

    results = await worker.run_until_idle(max_items=5)

    assert [result.status for result in results] == [
        WorkerRunStatus.COMPLETED,
        WorkerRunStatus.COMPLETED,
        WorkerRunStatus.NO_WORK,
    ]


@pytest.mark.asyncio
async def test_work_queue_worker_registers_and_heartbeats_with_worker_registry() -> None:
    queue = InMemoryWorkQueue()
    registry = InMemoryWorkerRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    current_time = now

    def clock() -> datetime:
        return current_time

    queue.enqueue(kind="agent_session", available_at=now)
    worker = WorkQueueWorker(
        queue=queue,
        worker_id=WorkerId("worker-a"),
        handlers={"agent_session": lambda item: WorkHandlerResult.completed()},
        worker_registry=registry,
        worker_ttl_seconds=20,
    )

    with runtime_primitives(clock=clock):
        completed = await worker.run_once()
        current_time = now + timedelta(seconds=5)
        idle = await worker.run_once()

    record = registry.get(WorkerId("worker-a"))
    assert completed.status is WorkerRunStatus.COMPLETED
    assert idle.status is WorkerRunStatus.NO_WORK
    assert record.registered_at == now
    assert record.heartbeat_at == now + timedelta(seconds=5)
    assert record.lease_expires_at == now + timedelta(seconds=25)
    assert record.capabilities == ("agent_session",)


@pytest.mark.asyncio
async def test_work_queue_worker_does_not_lease_when_worker_is_draining_or_offline() -> None:
    queue = InMemoryWorkQueue()
    registry = InMemoryWorkerRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    registry.register(WorkerId("worker-a"), now=now)
    registry.drain(WorkerId("worker-a"), now=now + timedelta(seconds=1))
    queue.enqueue(kind="agent_session", available_at=now)
    worker = WorkQueueWorker(
        queue=queue,
        worker_id=WorkerId("worker-a"),
        handlers={"agent_session": lambda item: WorkHandlerResult.completed()},
        worker_registry=registry,
    )

    draining = await worker.run_once()
    registry.mark_offline(WorkerId("worker-a"), now=now + timedelta(seconds=2))
    offline = await worker.run_once()

    assert draining.status is WorkerRunStatus.WORKER_INACTIVE
    assert draining.reason == "worker is draining"
    assert offline.status is WorkerRunStatus.WORKER_INACTIVE
    assert offline.reason == "worker is offline"
    assert queue.queued()[0].status is WorkItemStatus.QUEUED


@pytest.mark.asyncio
async def test_work_queue_worker_does_not_lease_after_worker_registry_expiry() -> None:
    queue = InMemoryWorkQueue()
    registry = InMemoryWorkerRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    current_time = now + timedelta(seconds=6)

    def clock() -> datetime:
        return current_time

    registry.register(WorkerId("worker-a"), ttl_seconds=5, now=now)
    queue.enqueue(kind="agent_session", available_at=now)
    worker = WorkQueueWorker(
        queue=queue,
        worker_id=WorkerId("worker-a"),
        handlers={"agent_session": lambda item: WorkHandlerResult.completed()},
        worker_registry=registry,
    )

    with runtime_primitives(clock=clock):
        result = await worker.run_once()

    assert result.status is WorkerRunStatus.WORKER_INACTIVE
    assert "expired" in result.reason
    assert registry.get(WorkerId("worker-a")).status is WorkerStatus.LOST
    assert queue.queued()[0].status is WorkItemStatus.QUEUED
