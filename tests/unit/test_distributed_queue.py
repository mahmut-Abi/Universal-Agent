from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from universal_agent.core import ActionId, SessionId, TaskId, immutable_json, runtime_primitives
from universal_agent.distributed import (
    FileWorkQueue,
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


@pytest.mark.parametrize(
    "terminal_status",
    (
        WorkItemStatus.COMPLETED,
        WorkItemStatus.FAILED,
        WorkItemStatus.CANCELLED,
    ),
)
def test_work_queue_idempotency_ignores_terminal_items(
    terminal_status: WorkItemStatus,
) -> None:
    queue = InMemoryWorkQueue()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    first = queue.enqueue(
        kind="agent_session",
        available_at=now,
        idempotency_key="session-1:goal-1",
    )

    if terminal_status is WorkItemStatus.CANCELLED:
        queue.cancel(first.work_item_id, reason="operator cancelled", now=now)
    else:
        leased = queue.lease(worker_id=WorkerId("worker-a"), now=now)
        assert leased.lease is not None
        if terminal_status is WorkItemStatus.COMPLETED:
            queue.complete(
                leased.lease.lease_id,
                worker_id=WorkerId("worker-a"),
                now=now + timedelta(seconds=1),
            )
        else:
            queue.fail(
                leased.lease.lease_id,
                worker_id=WorkerId("worker-a"),
                reason="terminal failure",
                retry=False,
                now=now + timedelta(seconds=1),
            )

    second = queue.enqueue(kind="agent_session", idempotency_key="session-1:goal-1")

    assert queue.get(first.work_item_id).status is terminal_status
    assert second.work_item_id == WorkItemId("work-2")
    assert second.status is WorkItemStatus.QUEUED


def test_file_work_queue_persists_items_and_lease_state(tmp_path: Path) -> None:
    path = tmp_path / "work-queue.json"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queue = FileWorkQueue(path)

    enqueued = queue.enqueue(
        kind="agent_session",
        payload=immutable_json({"goal": "verify workload"}),
        session_id=SessionId("session-1"),
        priority=5,
        available_at=now,
        idempotency_key="session:session-1",
    )
    leased = queue.lease(worker_id=WorkerId("worker-a"), ttl_seconds=10, now=now)
    assert leased.lease is not None
    renewed = queue.heartbeat(
        leased.lease.lease_id,
        worker_id=WorkerId("worker-a"),
        ttl_seconds=20,
        now=now + timedelta(seconds=5),
    )

    reloaded = FileWorkQueue(path)
    persisted = reloaded.get(enqueued.work_item_id)

    assert persisted.status is WorkItemStatus.LEASED
    assert persisted.payload["goal"] == "verify workload"
    assert persisted.session_id == SessionId("session-1")
    assert persisted.lease is not None
    assert renewed.lease is not None
    assert persisted.lease.lease_id == renewed.lease.lease_id
    assert persisted.lease.heartbeat_at == now + timedelta(seconds=5)
    assert persisted.lease.lease_expires_at == now + timedelta(seconds=25)


def test_file_work_queue_restores_sequence_and_idempotency(tmp_path: Path) -> None:
    path = tmp_path / "work-queue.json"
    queue = FileWorkQueue(path)
    first = queue.enqueue(kind="agent_session", idempotency_key="session:session-1")
    duplicate = FileWorkQueue(path).enqueue(
        kind="agent_session",
        idempotency_key="session:session-1",
    )
    second = FileWorkQueue(path).enqueue(kind="agent_session")

    assert duplicate.work_item_id == first.work_item_id
    assert second.work_item_id == WorkItemId("work-2")
    assert len(FileWorkQueue(path).queued()) == 2


def test_file_work_queue_reloads_before_reading_external_changes(tmp_path: Path) -> None:
    path = tmp_path / "work-queue.json"
    writer = FileWorkQueue(path)
    stale_reader = FileWorkQueue(path)

    enqueued = writer.enqueue(kind="agent_session", idempotency_key="session:session-1")

    assert stale_reader.get(enqueued.work_item_id).work_item_id == enqueued.work_item_id
    assert [item.work_item_id for item in stale_reader.queued()] == [enqueued.work_item_id]


def test_file_work_queue_reloads_before_stale_writer_mutates(tmp_path: Path) -> None:
    path = tmp_path / "work-queue.json"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    owner = FileWorkQueue(path)
    owner.enqueue(kind="agent_session", available_at=now)
    stale_writer = FileWorkQueue(path)

    leased = owner.lease(worker_id=WorkerId("worker-a"), ttl_seconds=30, now=now)
    enqueued = stale_writer.enqueue(kind="task", available_at=now)
    reloaded = FileWorkQueue(path)

    assert leased.lease is not None
    assert enqueued.work_item_id == WorkItemId("work-2")
    assert reloaded.get(leased.work_item_id).status is WorkItemStatus.LEASED
    assert reloaded.get(enqueued.work_item_id).kind == "task"


def test_file_work_queue_rejects_unsupported_file_version(tmp_path: Path) -> None:
    path = tmp_path / "work-queue.json"
    path.write_text(json.dumps({"version": 2, "items": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported file work queue version: 2"):
        FileWorkQueue(path)


def test_file_work_queue_rejects_duplicate_persisted_work_item_ids(tmp_path: Path) -> None:
    path = tmp_path / "work-queue.json"
    FileWorkQueue(path).enqueue(kind="agent_session")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    items = payload["items"]
    assert isinstance(items, list)
    items.append(items[0])
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate file work queue item: work-1"):
        FileWorkQueue(path)


def test_file_work_queue_persists_completion_and_expiry(tmp_path: Path) -> None:
    path = tmp_path / "work-queue.json"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queue = FileWorkQueue(path)
    queue.enqueue(kind="complete", max_attempts=2, available_at=now)
    queue.enqueue(kind="expire", max_attempts=1, available_at=now)

    completed_lease = queue.lease(worker_id=WorkerId("worker-a"), ttl_seconds=10, now=now)
    assert completed_lease.lease is not None
    completed = queue.complete(
        completed_lease.lease.lease_id,
        worker_id=WorkerId("worker-a"),
        now=now + timedelta(seconds=1),
    )
    expired_lease = queue.lease(worker_id=WorkerId("worker-b"), ttl_seconds=5, now=now)
    assert expired_lease.lease is not None
    expired = queue.expire(now=now + timedelta(seconds=6))

    reloaded = FileWorkQueue(path)

    assert reloaded.get(completed.work_item_id).status is WorkItemStatus.COMPLETED
    assert expired == (reloaded.get(expired_lease.work_item_id),)
    assert reloaded.get(expired_lease.work_item_id).status is WorkItemStatus.FAILED


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


def test_work_queue_leases_only_accepted_kinds_when_filter_is_provided() -> None:
    queue = InMemoryWorkQueue()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    unsupported = queue.enqueue(kind="tool_action", priority=10, available_at=now)
    supported = queue.enqueue(kind="agent_session", priority=1, available_at=now)

    leased = queue.lease(
        worker_id=WorkerId("worker-a"),
        now=now,
        accepted_kinds=("agent_session",),
    )

    assert leased.work_item_id == supported.work_item_id
    assert queue.get(unsupported.work_item_id).status is WorkItemStatus.QUEUED
    with pytest.raises(NoWorkAvailable):
        queue.lease(
            worker_id=WorkerId("worker-b"),
            now=now,
            accepted_kinds=("task",),
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
    with pytest.raises(ValueError, match="accepted_kinds"):
        queue.lease(worker_id=WorkerId("worker-a"), accepted_kinds=(" ",))


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
            "agent_session": lambda item: WorkHandlerResult.completed(f"handled {item.kind}")
        },
    )
    result = await worker.run_once()

    assert result.status is WorkerRunStatus.COMPLETED
    assert result.work_item is not None
    assert result.work_item.status is WorkItemStatus.COMPLETED
    assert result.reason == "handled agent_session"


@pytest.mark.asyncio
async def test_work_queue_worker_leases_only_declared_capabilities() -> None:
    queue = InMemoryWorkQueue()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    unsupported = queue.enqueue(kind="tool_action", priority=10, available_at=now)
    supported = queue.enqueue(kind="agent_session", priority=1, available_at=now)
    worker = WorkQueueWorker(
        queue=queue,
        worker_id=WorkerId("worker-a"),
        handlers={"agent_session": lambda item: WorkHandlerResult.completed("handled session")},
    )

    result = await worker.run_once()

    assert result.status is WorkerRunStatus.COMPLETED
    assert result.work_item is not None
    assert result.work_item.work_item_id == supported.work_item_id
    assert queue.get(unsupported.work_item_id).status is WorkItemStatus.QUEUED


@pytest.mark.asyncio
async def test_work_queue_worker_heartbeats_long_running_async_handler() -> None:
    queue = InMemoryWorkQueue()
    registry = InMemoryWorkerRegistry()
    queue.enqueue(kind="agent_session")

    async def handler(item: WorkItem) -> WorkHandlerResult:
        await asyncio.sleep(0.25)
        return WorkHandlerResult.completed("long handler completed")

    worker = WorkQueueWorker(
        queue=queue,
        worker_id=WorkerId("worker-a"),
        handlers={"agent_session": handler},
        lease_ttl_seconds=0.12,
        heartbeat_interval_seconds=0.03,
        worker_registry=registry,
        worker_ttl_seconds=0.12,
    )

    result = await worker.run_once()

    record = registry.get(WorkerId("worker-a"))
    assert result.status is WorkerRunStatus.COMPLETED
    assert result.work_item is not None
    assert result.work_item.status is WorkItemStatus.COMPLETED
    assert record.heartbeat_at > record.registered_at


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
    worker = WorkQueueWorker(
        queue=queue,
        worker_id=WorkerId("worker-a"),
        handlers={},
        worker_capabilities=("missing_handler",),
    )

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
