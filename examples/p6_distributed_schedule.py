from __future__ import annotations

from datetime import UTC, datetime

from universal_agent.core import SessionId, immutable_json
from universal_agent.distributed import DistributedRuntimeCoordinator, WorkerId


def main() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()
    coordinator.workers.register(
        WorkerId("agent-worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=30,
        now=now,
    )

    result = coordinator.schedule_session(
        SessionId("session-1"),
        payload=immutable_json({"goal": "verify workload health"}),
        priority=5,
        max_attempts=2,
        available_at=now,
        now=now,
    )

    print(f"work_item_id={result.scheduled_work_item.work_item_id}")
    print(f"kind={result.scheduled_work_item.kind}")
    print(f"queued={result.snapshot.work_queue.queued_count}")
    print(f"health={result.health.status.value}")


if __name__ == "__main__":
    main()
