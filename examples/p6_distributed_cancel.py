from __future__ import annotations

from datetime import UTC, datetime

from universal_agent.core import SessionId
from universal_agent.distributed import DistributedRuntimeCoordinator, WorkerId


def main() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()

    scheduled = coordinator.scheduler.schedule_session(
        SessionId("session-1"),
        available_at=now,
    )
    coordinator.workers.register(
        WorkerId("agent-worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=30,
        now=now,
    )

    result = coordinator.cancel_work_item(
        scheduled.work_item_id,
        reason="operator cancelled queued session work",
        now=now,
    )

    print(f"work_item_id={result.cancelled_work_item.work_item_id}")
    print(f"status={result.cancelled_work_item.status.value}")
    print(f"queued={result.snapshot.work_queue.queued_count}")
    print(f"cancelled={result.snapshot.work_queue.cancelled_count}")
    print(f"health={result.health.status.value}")


if __name__ == "__main__":
    main()
