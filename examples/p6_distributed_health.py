from __future__ import annotations

from datetime import UTC, datetime, timedelta

from universal_agent.core import SessionId
from universal_agent.distributed import (
    DistributedLockOwnerId,
    InMemoryDistributedLockRegistry,
    InMemoryWorkerRegistry,
    InMemoryWorkQueue,
    WorkerId,
    WorkScheduler,
    build_distributed_health_report,
    build_distributed_runtime_snapshot,
)


def main() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queue = InMemoryWorkQueue()
    scheduler = WorkScheduler(queue)
    workers = InMemoryWorkerRegistry()
    locks = InMemoryDistributedLockRegistry()

    scheduler.schedule_session(SessionId("session-1"), priority=10, available_at=now)
    workers.register(
        WorkerId("agent-worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=6,
        now=now,
    )
    queue.lease(worker_id=WorkerId("agent-worker-a"), ttl_seconds=6, now=now)
    locks.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("agent-worker-a"),
        ttl_seconds=6,
        now=now,
    )

    snapshot = build_distributed_runtime_snapshot(queue=queue, locks=locks, workers=workers)
    report = build_distributed_health_report(
        snapshot,
        now=now + timedelta(seconds=2),
        queued_backlog_warn_threshold=0,
        lease_expiry_warn_seconds=5,
    )

    print(f"status={report.status.value}")
    print("checks=" + ",".join(f"{check.name}:{check.status.value}" for check in report.checks))
    print(f"expiring_leases={len(report.expiring_leases)}")
    print(
        "recommendations="
        + ",".join(
            f"{item.code}:{item.severity.value}:{item.target or '-'}"
            for item in report.recommendations
        )
    )


if __name__ == "__main__":
    main()
