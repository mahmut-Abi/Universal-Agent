from __future__ import annotations

from datetime import UTC, datetime, timedelta

from universal_agent.core import SessionId, TaskId, immutable_json
from universal_agent.distributed import InMemoryWorkQueue, WorkerId, WorkItemStatus


def main() -> None:
    queue = InMemoryWorkQueue()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    queue.enqueue(
        kind="agent_session",
        payload=immutable_json({"goal": "verify workload health"}),
        session_id=SessionId("session-1"),
        task_id=TaskId("task-1"),
        priority=10,
        max_attempts=2,
        available_at=now,
        idempotency_key="session-1:task-1",
    )

    first = queue.lease(worker_id=WorkerId("agent-worker-a"), ttl_seconds=30, now=now)
    assert first.lease is not None
    renewed = queue.heartbeat(
        first.lease.lease_id,
        worker_id=WorkerId("agent-worker-a"),
        ttl_seconds=30,
        now=now + timedelta(seconds=10),
    )
    assert renewed.lease is not None

    retry = queue.fail(
        renewed.lease.lease_id,
        worker_id=WorkerId("agent-worker-a"),
        reason="worker restarted",
        retry=True,
        now=now + timedelta(seconds=11),
    )
    assert retry.status is WorkItemStatus.QUEUED

    second = queue.lease(worker_id=WorkerId("agent-worker-b"), now=now + timedelta(seconds=12))
    assert second.lease is not None
    completed = queue.complete(
        second.lease.lease_id,
        worker_id=WorkerId("agent-worker-b"),
        now=now + timedelta(seconds=20),
    )

    print(f"work_item={completed.work_item_id}")
    print(f"status={completed.status.value}")
    print(f"attempts={completed.attempts}")
    print(f"queued={len(queue.queued())}")
    print(f"leased={len(queue.leased())}")


if __name__ == "__main__":
    main()
