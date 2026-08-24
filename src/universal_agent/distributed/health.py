from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from universal_agent.distributed.queue import WorkItemStatus
from universal_agent.distributed.snapshot import (
    DistributedLockSnapshot,
    DistributedRuntimeSnapshot,
    WorkerSnapshot,
    WorkItemSnapshot,
)
from universal_agent.distributed.worker_state import WorkerStatus


class DistributedHealthStatus(StrEnum):
    OK = "ok"
    WARN = "warn"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DistributedHealthCheck:
    name: str
    status: DistributedHealthStatus
    message: str


@dataclass(frozen=True, slots=True)
class DistributedCapacityGap:
    kind: str
    queued_count: int
    capable_online_workers: int


@dataclass(frozen=True, slots=True)
class DistributedExpiringLease:
    lease_type: str
    key: str
    owner_id: str | None
    lease_expires_at: datetime
    seconds_remaining: float


@dataclass(frozen=True, slots=True)
class DistributedHealthRecommendation:
    code: str
    severity: DistributedHealthStatus
    target: str | None
    message: str


@dataclass(frozen=True, slots=True)
class DistributedHealthReport:
    status: DistributedHealthStatus
    checks: tuple[DistributedHealthCheck, ...]
    capacity_gaps: tuple[DistributedCapacityGap, ...]
    expiring_leases: tuple[DistributedExpiringLease, ...]
    recommendations: tuple[DistributedHealthRecommendation, ...]


def build_distributed_health_report(
    snapshot: DistributedRuntimeSnapshot,
    *,
    now: datetime,
    queued_backlog_warn_threshold: int = 100,
    lease_expiry_warn_seconds: float = 10.0,
    min_online_workers: int = 1,
) -> DistributedHealthReport:
    """Project a read-only P6 coordination snapshot into HA-oriented checks."""

    if queued_backlog_warn_threshold < 0:
        raise ValueError("queued_backlog_warn_threshold must be non-negative")
    if lease_expiry_warn_seconds < 0:
        raise ValueError("lease_expiry_warn_seconds must be non-negative")
    if min_online_workers < 0:
        raise ValueError("min_online_workers must be non-negative")

    capacity_gaps = _capacity_gaps(snapshot)
    expiring_leases = _expiring_leases(
        snapshot,
        now=now,
        warn_seconds=lease_expiry_warn_seconds,
    )
    checks = (
        _worker_pool_check(snapshot, min_online_workers=min_online_workers),
        _queue_backlog_check(
            snapshot,
            queued_backlog_warn_threshold=queued_backlog_warn_threshold,
        ),
        _capacity_check(capacity_gaps),
        _lease_freshness_check(expiring_leases),
        _leased_work_owner_check(snapshot),
        _worker_registry_check(snapshot),
    )
    return DistributedHealthReport(
        status=_aggregate_status(checks),
        checks=checks,
        capacity_gaps=capacity_gaps,
        expiring_leases=expiring_leases,
        recommendations=_recommendations(
            checks=checks,
            capacity_gaps=capacity_gaps,
            expiring_leases=expiring_leases,
        ),
    )


def _worker_pool_check(
    snapshot: DistributedRuntimeSnapshot,
    *,
    min_online_workers: int,
) -> DistributedHealthCheck:
    active_work_count = snapshot.work_queue.queued_count + snapshot.work_queue.leased_count
    online_count = snapshot.workers.online_count
    if active_work_count and online_count < min_online_workers:
        return DistributedHealthCheck(
            "worker_pool",
            DistributedHealthStatus.ERROR,
            "online workers below required capacity: "
            f"online={online_count} required={min_online_workers} "
            f"active_work={active_work_count}",
        )
    return DistributedHealthCheck(
        "worker_pool",
        DistributedHealthStatus.OK,
        f"online workers={online_count} required={min_online_workers}",
    )


def _queue_backlog_check(
    snapshot: DistributedRuntimeSnapshot,
    *,
    queued_backlog_warn_threshold: int,
) -> DistributedHealthCheck:
    queued_count = snapshot.work_queue.queued_count
    if queued_count > queued_backlog_warn_threshold:
        return DistributedHealthCheck(
            "queue_backlog",
            DistributedHealthStatus.WARN,
            f"queued work exceeds threshold: queued={queued_count} "
            f"threshold={queued_backlog_warn_threshold}",
        )
    return DistributedHealthCheck(
        "queue_backlog",
        DistributedHealthStatus.OK,
        f"queued work={queued_count} threshold={queued_backlog_warn_threshold}",
    )


def _capacity_check(
    capacity_gaps: tuple[DistributedCapacityGap, ...],
) -> DistributedHealthCheck:
    if not capacity_gaps:
        return DistributedHealthCheck(
            "capacity",
            DistributedHealthStatus.OK,
            "queued work kinds have online capable workers",
        )
    kinds = ",".join(gap.kind for gap in capacity_gaps)
    return DistributedHealthCheck(
        "capacity",
        DistributedHealthStatus.ERROR,
        f"queued work has no online capable worker: {kinds}",
    )


def _lease_freshness_check(
    expiring_leases: tuple[DistributedExpiringLease, ...],
) -> DistributedHealthCheck:
    expired_count = sum(1 for lease in expiring_leases if lease.seconds_remaining <= 0)
    if expired_count:
        return DistributedHealthCheck(
            "lease_freshness",
            DistributedHealthStatus.ERROR,
            f"expired leases visible in snapshot: {expired_count}",
        )
    if expiring_leases:
        return DistributedHealthCheck(
            "lease_freshness",
            DistributedHealthStatus.WARN,
            f"leases expiring soon: {len(expiring_leases)}",
        )
    return DistributedHealthCheck(
        "lease_freshness",
        DistributedHealthStatus.OK,
        "no leases are expired or close to expiry",
    )


def _leased_work_owner_check(snapshot: DistributedRuntimeSnapshot) -> DistributedHealthCheck:
    worker_by_id = {worker.worker_id: worker for worker in snapshot.workers.workers}
    orphaned: list[str] = []
    draining: list[str] = []
    for item in snapshot.work_queue.items:
        if item.status is not WorkItemStatus.LEASED:
            continue
        if item.worker_id is None:
            orphaned.append(str(item.work_item_id))
            continue
        worker = worker_by_id.get(item.worker_id)
        if worker is None:
            orphaned.append(str(item.work_item_id))
        elif worker.status in {WorkerStatus.OFFLINE, WorkerStatus.LOST}:
            orphaned.append(str(item.work_item_id))
        elif worker.status is WorkerStatus.DRAINING:
            draining.append(str(item.work_item_id))
    if orphaned:
        return DistributedHealthCheck(
            "leased_work_owners",
            DistributedHealthStatus.ERROR,
            "leased work has missing/offline/lost owners: " + ",".join(sorted(orphaned)),
        )
    if draining:
        return DistributedHealthCheck(
            "leased_work_owners",
            DistributedHealthStatus.WARN,
            "leased work is still owned by draining workers: " + ",".join(sorted(draining)),
        )
    return DistributedHealthCheck(
        "leased_work_owners",
        DistributedHealthStatus.OK,
        "leased work owners are online",
    )


def _worker_registry_check(snapshot: DistributedRuntimeSnapshot) -> DistributedHealthCheck:
    lost = snapshot.workers.lost_count
    offline = snapshot.workers.offline_count
    draining = snapshot.workers.draining_count
    if lost:
        return DistributedHealthCheck(
            "worker_registry",
            DistributedHealthStatus.WARN,
            f"lost workers observed: lost={lost} offline={offline} draining={draining}",
        )
    if offline or draining:
        return DistributedHealthCheck(
            "worker_registry",
            DistributedHealthStatus.WARN,
            f"inactive workers observed: offline={offline} draining={draining}",
        )
    return DistributedHealthCheck(
        "worker_registry",
        DistributedHealthStatus.OK,
        f"workers registered={snapshot.workers.total_count}",
    )


def _recommendations(
    *,
    checks: tuple[DistributedHealthCheck, ...],
    capacity_gaps: tuple[DistributedCapacityGap, ...],
    expiring_leases: tuple[DistributedExpiringLease, ...],
) -> tuple[DistributedHealthRecommendation, ...]:
    check_by_name = {check.name: check for check in checks}
    recommendations: list[DistributedHealthRecommendation] = []
    worker_pool = check_by_name["worker_pool"]
    if worker_pool.status is DistributedHealthStatus.ERROR:
        recommendations.append(
            DistributedHealthRecommendation(
                code="start_worker_pool",
                severity=DistributedHealthStatus.ERROR,
                target=None,
                message="register at least one online worker before processing active work",
            )
        )
    queue_backlog = check_by_name["queue_backlog"]
    if queue_backlog.status is DistributedHealthStatus.WARN:
        recommendations.append(
            DistributedHealthRecommendation(
                code="drain_queue_backlog",
                severity=DistributedHealthStatus.WARN,
                target=None,
                message="run capable workers or raise worker capacity to reduce queued backlog",
            )
        )
    for gap in capacity_gaps:
        recommendations.append(
            DistributedHealthRecommendation(
                code="start_capable_worker",
                severity=DistributedHealthStatus.ERROR,
                target=gap.kind,
                message=f"start or register an online worker that handles {gap.kind}",
            )
        )
    expired_count = sum(1 for lease in expiring_leases if lease.seconds_remaining <= 0)
    if expired_count:
        recommendations.append(
            DistributedHealthRecommendation(
                code="run_expiry_sweep",
                severity=DistributedHealthStatus.ERROR,
                target=None,
                message="run distributed expire to reconcile expired work, lock and worker leases",
            )
        )
    elif expiring_leases:
        recommendations.append(
            DistributedHealthRecommendation(
                code="renew_expiring_leases",
                severity=DistributedHealthStatus.WARN,
                target=None,
                message="heartbeat active leases or run distributed expire before leases lapse",
            )
        )
    leased_work_owners = check_by_name["leased_work_owners"]
    if leased_work_owners.status is not DistributedHealthStatus.OK:
        recommendations.append(
            DistributedHealthRecommendation(
                code="inspect_leased_work_owners",
                severity=leased_work_owners.status,
                target=None,
                message="inspect leased work owners before retrying or cancelling affected work",
            )
        )
    worker_registry = check_by_name["worker_registry"]
    if worker_registry.status is not DistributedHealthStatus.OK:
        recommendations.append(
            DistributedHealthRecommendation(
                code="inspect_worker_registry",
                severity=worker_registry.status,
                target=None,
                message=(
                    "inspect lost, offline or draining workers and restart or mark them offline"
                ),
            )
        )
    return tuple(recommendations)


def _capacity_gaps(snapshot: DistributedRuntimeSnapshot) -> tuple[DistributedCapacityGap, ...]:
    queued_by_kind = Counter(
        item.kind for item in snapshot.work_queue.items if item.status is WorkItemStatus.QUEUED
    )
    online_workers = tuple(
        worker for worker in snapshot.workers.workers if worker.status is WorkerStatus.ONLINE
    )
    gaps: list[DistributedCapacityGap] = []
    for kind, queued_count in sorted(queued_by_kind.items()):
        capable_worker_count = _capable_worker_count(online_workers, kind)
        if capable_worker_count == 0:
            gaps.append(
                DistributedCapacityGap(
                    kind=kind,
                    queued_count=queued_count,
                    capable_online_workers=capable_worker_count,
                )
            )
    return tuple(gaps)


def _capable_worker_count(workers: tuple[WorkerSnapshot, ...], kind: str) -> int:
    return sum(1 for worker in workers if kind in worker.capabilities)


def _expiring_leases(
    snapshot: DistributedRuntimeSnapshot,
    *,
    now: datetime,
    warn_seconds: float,
) -> tuple[DistributedExpiringLease, ...]:
    all_leases = (
        _work_leases(snapshot.work_queue.items, now=now)
        + _lock_leases(snapshot.locks, now=now)
        + _worker_leases(snapshot.workers.workers, now=now)
    )
    leases = tuple(lease for lease in all_leases if lease.seconds_remaining <= warn_seconds)
    return tuple(
        sorted(
            leases,
            key=lambda lease: (lease.seconds_remaining, lease.lease_type, lease.key),
        )
    )


def _work_leases(
    items: tuple[WorkItemSnapshot, ...],
    *,
    now: datetime,
) -> tuple[DistributedExpiringLease, ...]:
    leases: list[DistributedExpiringLease] = []
    for item in items:
        if item.status is not WorkItemStatus.LEASED or item.lease_expires_at is None:
            continue
        leases.append(
            DistributedExpiringLease(
                lease_type="work_item",
                key=str(item.work_item_id),
                owner_id=None if item.worker_id is None else str(item.worker_id),
                lease_expires_at=item.lease_expires_at,
                seconds_remaining=_seconds_remaining(item.lease_expires_at, now),
            )
        )
    return tuple(leases)


def _lock_leases(
    locks: tuple[DistributedLockSnapshot, ...],
    *,
    now: datetime,
) -> tuple[DistributedExpiringLease, ...]:
    return tuple(
        DistributedExpiringLease(
            lease_type="distributed_lock",
            key=lock.lock_key,
            owner_id=str(lock.owner_id),
            lease_expires_at=lock.lease_expires_at,
            seconds_remaining=_seconds_remaining(lock.lease_expires_at, now),
        )
        for lock in locks
    )


def _worker_leases(
    workers: tuple[WorkerSnapshot, ...],
    *,
    now: datetime,
) -> tuple[DistributedExpiringLease, ...]:
    return tuple(
        DistributedExpiringLease(
            lease_type="worker",
            key=str(worker.worker_id),
            owner_id=str(worker.worker_id),
            lease_expires_at=worker.lease_expires_at,
            seconds_remaining=_seconds_remaining(worker.lease_expires_at, now),
        )
        for worker in workers
        if worker.status in {WorkerStatus.ONLINE, WorkerStatus.DRAINING}
    )


def _seconds_remaining(lease_expires_at: datetime, now: datetime) -> float:
    return round((lease_expires_at - now).total_seconds(), 3)


def _aggregate_status(
    checks: tuple[DistributedHealthCheck, ...],
) -> DistributedHealthStatus:
    if any(check.status is DistributedHealthStatus.ERROR for check in checks):
        return DistributedHealthStatus.ERROR
    if any(check.status is DistributedHealthStatus.WARN for check in checks):
        return DistributedHealthStatus.WARN
    return DistributedHealthStatus.OK
