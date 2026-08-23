from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from universal_agent.core import ActionId, SessionId, TaskId, immutable_json
from universal_agent.distributed import (
    InMemoryWorkQueue,
    LeaseLostError,
    NoWorkAvailable,
    WorkerId,
    WorkItemId,
    WorkItemStatus,
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


def test_work_queue_validates_inputs() -> None:
    queue = InMemoryWorkQueue()

    with pytest.raises(ValueError, match="kind"):
        queue.enqueue(kind=" ")
    with pytest.raises(ValueError, match="max_attempts"):
        queue.enqueue(kind="agent_session", max_attempts=0)
    queue.enqueue(kind="agent_session")
    with pytest.raises(ValueError, match="ttl_seconds"):
        queue.lease(worker_id=WorkerId("worker-a"), ttl_seconds=0)
