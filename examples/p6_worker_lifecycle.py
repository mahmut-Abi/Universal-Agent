from __future__ import annotations

from datetime import UTC, datetime, timedelta

from universal_agent.distributed import DistributedRuntimeCoordinator, WorkerId


def main() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()

    registered = coordinator.register_worker(
        WorkerId("agent-worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=30,
        now=now,
    )
    heartbeat = coordinator.heartbeat_worker(
        WorkerId("agent-worker-a"),
        ttl_seconds=60,
        now=now + timedelta(seconds=5),
    )
    draining = coordinator.drain_worker(
        WorkerId("agent-worker-a"),
        reason="finish current lease",
        now=now + timedelta(seconds=6),
    )
    offline = coordinator.mark_worker_offline(
        WorkerId("agent-worker-a"),
        reason="shutdown complete",
        now=now + timedelta(seconds=7),
    )

    print(f"registered={registered.worker.status.value}")
    print(f"heartbeat={heartbeat.worker.heartbeat_at.isoformat()}")
    print(f"draining={draining.worker.last_error}")
    print(f"offline={offline.worker.status.value}")
    print(f"offline_count={offline.snapshot.workers.offline_count}")


if __name__ == "__main__":
    main()
