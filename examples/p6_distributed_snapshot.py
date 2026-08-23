from __future__ import annotations

from datetime import UTC, datetime

from universal_agent.core import ActionId, SessionId, TaskId
from universal_agent.distributed import (
    DistributedLockOwnerId,
    InMemoryDistributedLockRegistry,
    InMemoryWorkerRegistry,
    InMemoryWorkQueue,
    WorkerId,
    WorkScheduler,
    build_distributed_runtime_snapshot,
)


def main() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queue = InMemoryWorkQueue()
    scheduler = WorkScheduler(queue)
    locks = InMemoryDistributedLockRegistry()
    workers = InMemoryWorkerRegistry()

    scheduler.schedule_session(SessionId("session-1"), priority=10, available_at=now)
    scheduler.schedule_action(
        SessionId("session-1"),
        TaskId("task-1"),
        ActionId("action-1"),
        priority=5,
        available_at=now,
    )
    queue.lease(worker_id=WorkerId("agent-worker-a"), ttl_seconds=30, now=now)
    locks.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("agent-worker-a"),
        ttl_seconds=30,
        now=now,
    )
    workers.register(
        WorkerId("agent-worker-a"),
        capabilities=("agent_session", "tool_action"),
        ttl_seconds=30,
        now=now,
    )

    snapshot = build_distributed_runtime_snapshot(queue=queue, locks=locks, workers=workers)

    print(f"work_total={snapshot.work_queue.total_count}")
    print(f"work_leased={snapshot.work_queue.leased_count}")
    print(f"locks={len(snapshot.locks)}")
    print(f"workers_online={snapshot.workers.online_count}")


if __name__ == "__main__":
    main()
