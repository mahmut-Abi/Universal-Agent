from __future__ import annotations

from datetime import UTC, datetime, timedelta

from universal_agent.core import SessionId
from universal_agent.distributed import DistributedRuntimeCoordinator, WorkerId


def main() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()

    retained = coordinator.scheduler.schedule_session(
        SessionId("session-retained"),
        available_at=now,
    )
    coordinator.workers.register(
        WorkerId("agent-worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=30,
        now=now,
    )
    coordinator.queue.enqueue(kind="completed-session", priority=10, available_at=now)
    coordinator.queue.enqueue(kind="failed-session", priority=9, max_attempts=1, available_at=now)
    cancelled = coordinator.scheduler.schedule_session(
        SessionId("session-cancelled"),
        available_at=now,
    )

    completed_lease = coordinator.queue.lease(worker_id=WorkerId("agent-worker-a"), now=now)
    failed_lease = coordinator.queue.lease(worker_id=WorkerId("agent-worker-b"), now=now)
    assert completed_lease.lease is not None
    assert failed_lease.lease is not None

    coordinator.queue.complete(
        completed_lease.lease.lease_id,
        worker_id=WorkerId("agent-worker-a"),
        now=now + timedelta(seconds=1),
    )
    coordinator.queue.fail(
        failed_lease.lease.lease_id,
        worker_id=WorkerId("agent-worker-b"),
        reason="terminal failure",
        retry=False,
        now=now + timedelta(seconds=2),
    )
    coordinator.cancel_work_item(
        cancelled.work_item_id,
        reason="operator cancelled old work",
        now=now + timedelta(seconds=3),
    )

    pruned = coordinator.prune_terminal_work_items(
        before=now + timedelta(seconds=2),
        now=now + timedelta(seconds=4),
    )

    print(f"pruned={len(pruned.pruned_work_items)}")
    print(f"retained_work_item_id={retained.work_item_id}")
    print(f"remaining={pruned.snapshot.work_queue.total_count}")
    print(f"queued={pruned.snapshot.work_queue.queued_count}")
    print(f"cancelled={pruned.snapshot.work_queue.cancelled_count}")
    print(f"health={pruned.health.status.value}")


if __name__ == "__main__":
    main()
