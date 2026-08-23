from __future__ import annotations

from datetime import UTC, datetime, timedelta

from universal_agent.distributed import DistributedLockOwnerId, DistributedRuntimeCoordinator


def main() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()

    acquired = coordinator.acquire_lock(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=30,
        now=now,
    )
    heartbeat = coordinator.heartbeat_lock(
        acquired.lock.lease_id,
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=60,
        now=now + timedelta(seconds=5),
    )
    released = coordinator.release_lock(
        heartbeat.lock.lease_id,
        owner_id=DistributedLockOwnerId("worker-a"),
        now=now + timedelta(seconds=6),
    )

    print(f"lock_key={acquired.lock.lock_key}")
    print(f"lease_id={heartbeat.lock.lease_id}")
    print(f"active_after_release={len(released.snapshot.locks)}")
    print(f"health={released.health.status.value}")


if __name__ == "__main__":
    main()
