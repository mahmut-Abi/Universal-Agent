from __future__ import annotations

from datetime import UTC, datetime, timedelta

from universal_agent.distributed import (
    DistributedLockConflictError,
    DistributedLockOwnerId,
    InMemoryDistributedLockRegistry,
)


def main() -> None:
    registry = InMemoryDistributedLockRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    lease = registry.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=30,
        now=now,
    )
    try:
        registry.acquire(
            lock_key="session/session-1",
            owner_id=DistributedLockOwnerId("worker-b"),
            now=now + timedelta(seconds=1),
        )
    except DistributedLockConflictError as exc:
        print(f"conflict={exc}")

    renewed = registry.heartbeat(
        lease.lease_id,
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=30,
        now=now + timedelta(seconds=10),
    )
    released = registry.release(
        renewed.lease_id,
        owner_id=DistributedLockOwnerId("worker-a"),
        now=now + timedelta(seconds=11),
    )

    print(f"lease_id={released.lease_id}")
    print(f"owner={released.owner_id}")
    print(f"active={len(registry.active())}")


if __name__ == "__main__":
    main()
