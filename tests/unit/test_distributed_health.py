from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from universal_agent.core import ActionId, SessionId, TaskId
from universal_agent.distributed import (
    DistributedHealthStatus,
    DistributedLockOwnerId,
    InMemoryDistributedLockRegistry,
    InMemoryWorkerRegistry,
    InMemoryWorkQueue,
    WorkerId,
    WorkScheduler,
    build_distributed_health_report,
    build_distributed_runtime_snapshot,
)


def test_distributed_health_report_is_ok_when_work_has_capable_workers() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queue = InMemoryWorkQueue()
    scheduler = WorkScheduler(queue)
    workers = InMemoryWorkerRegistry()

    scheduler.schedule_session(SessionId("session-1"), available_at=now)
    workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=30,
        now=now,
    )

    snapshot = build_distributed_runtime_snapshot(queue=queue, workers=workers)
    report = build_distributed_health_report(snapshot, now=now)
    checks = {check.name: check.status for check in report.checks}

    assert report.status is DistributedHealthStatus.OK
    assert report.capacity_gaps == ()
    assert report.expiring_leases == ()
    assert checks["capacity"] is DistributedHealthStatus.OK


def test_distributed_health_report_detects_missing_worker_capacity() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queue = InMemoryWorkQueue()
    scheduler = WorkScheduler(queue)
    workers = InMemoryWorkerRegistry()

    scheduler.schedule_action(
        SessionId("session-1"),
        TaskId("task-1"),
        ActionId("action-1"),
        available_at=now,
    )
    workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=30,
        now=now,
    )

    snapshot = build_distributed_runtime_snapshot(queue=queue, workers=workers)
    report = build_distributed_health_report(snapshot, now=now)
    checks = {check.name: check.status for check in report.checks}

    assert report.status is DistributedHealthStatus.ERROR
    assert [
        (gap.kind, gap.queued_count, gap.capable_online_workers) for gap in report.capacity_gaps
    ] == [("tool_action", 1, 0)]
    assert checks["capacity"] is DistributedHealthStatus.ERROR


def test_distributed_health_report_warns_on_backlog_and_expiring_leases() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queue = InMemoryWorkQueue()
    scheduler = WorkScheduler(queue)
    locks = InMemoryDistributedLockRegistry()
    workers = InMemoryWorkerRegistry()

    scheduler.schedule_session(SessionId("session-1"), available_at=now)
    scheduler.schedule_session(SessionId("session-2"), available_at=now)
    workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=6,
        now=now,
    )
    leased = queue.lease(worker_id=WorkerId("worker-a"), ttl_seconds=6, now=now)
    locks.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=6,
        now=now,
    )

    snapshot = build_distributed_runtime_snapshot(queue=queue, locks=locks, workers=workers)
    report = build_distributed_health_report(
        snapshot,
        now=now + timedelta(seconds=1),
        queued_backlog_warn_threshold=0,
        lease_expiry_warn_seconds=5,
    )
    checks = {check.name: check.status for check in report.checks}

    assert leased.lease is not None
    assert leased.lease.worker_id == WorkerId("worker-a")
    assert report.status is DistributedHealthStatus.WARN
    assert checks["queue_backlog"] is DistributedHealthStatus.WARN
    assert checks["lease_freshness"] is DistributedHealthStatus.WARN
    assert [(lease.lease_type, lease.key) for lease in report.expiring_leases] == [
        ("distributed_lock", "session/session-1"),
        ("work_item", str(leased.work_item_id)),
        ("worker", "worker-a"),
    ]


def test_distributed_health_report_detects_stale_leases_and_lost_workers() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queue = InMemoryWorkQueue()
    workers = InMemoryWorkerRegistry()

    queue.enqueue(kind="agent_session", available_at=now)
    queue.lease(worker_id=WorkerId("worker-a"), ttl_seconds=5, now=now)
    workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=5,
        now=now,
    )
    workers.expire(now=now + timedelta(seconds=6))

    snapshot = build_distributed_runtime_snapshot(queue=queue, workers=workers)
    report = build_distributed_health_report(snapshot, now=now + timedelta(seconds=6))
    checks = {check.name: check.status for check in report.checks}

    assert report.status is DistributedHealthStatus.ERROR
    assert checks["lease_freshness"] is DistributedHealthStatus.ERROR
    assert checks["leased_work_owners"] is DistributedHealthStatus.ERROR
    assert checks["worker_registry"] is DistributedHealthStatus.WARN
    assert report.expiring_leases[0].seconds_remaining == -1.0


def test_distributed_health_report_validates_thresholds() -> None:
    snapshot = build_distributed_runtime_snapshot(queue=InMemoryWorkQueue())
    now = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(ValueError, match="queued_backlog_warn_threshold"):
        build_distributed_health_report(snapshot, now=now, queued_backlog_warn_threshold=-1)
    with pytest.raises(ValueError, match="lease_expiry_warn_seconds"):
        build_distributed_health_report(snapshot, now=now, lease_expiry_warn_seconds=-1)
    with pytest.raises(ValueError, match="min_online_workers"):
        build_distributed_health_report(snapshot, now=now, min_online_workers=-1)
