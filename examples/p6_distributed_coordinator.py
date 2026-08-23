from __future__ import annotations

from datetime import UTC, datetime, timedelta

from universal_agent.core import SessionId
from universal_agent.distributed import (
    DistributedLockOwnerId,
    DistributedRuntimeCoordinator,
    WorkerId,
)


def main() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()

    coordinator.scheduler.schedule_session(SessionId("session-1"), available_at=now)
    coordinator.workers.register(
        WorkerId("agent-worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=5,
        now=now,
    )
    coordinator.queue.lease(worker_id=WorkerId("agent-worker-a"), ttl_seconds=5, now=now)
    coordinator.locks.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("agent-worker-a"),
        ttl_seconds=5,
        now=now,
    )

    before = coordinator.snapshot()
    sweep = coordinator.expire(now=now + timedelta(seconds=6))

    print(f"before_leased={before.work_queue.leased_count}")
    print(f"expired_work={len(sweep.expired_work_items)}")
    print(f"expired_locks={len(sweep.expired_locks)}")
    print(f"expired_workers={len(sweep.expired_workers)}")
    print(f"health={sweep.health.status.value}")


if __name__ == "__main__":
    main()
