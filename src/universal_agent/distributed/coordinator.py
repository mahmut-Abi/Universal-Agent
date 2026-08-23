from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from universal_agent.core import JsonMapping, SessionId, utc_now
from universal_agent.distributed.health import (
    DistributedHealthReport,
    build_distributed_health_report,
)
from universal_agent.distributed.locks import (
    DistributedLockLease,
    DistributedLockLeaseId,
    DistributedLockOwnerId,
    InMemoryDistributedLockRegistry,
)
from universal_agent.distributed.queue import InMemoryWorkQueue, WorkerId, WorkItem, WorkItemId
from universal_agent.distributed.scheduler import WorkScheduler
from universal_agent.distributed.snapshot import (
    DistributedRuntimeSnapshot,
    build_distributed_runtime_snapshot,
)
from universal_agent.distributed.worker_state import InMemoryWorkerRegistry, WorkerRecord


@dataclass(frozen=True, slots=True)
class DistributedMaintenanceResult:
    ran_at: datetime
    expired_work_items: tuple[WorkItem, ...]
    expired_locks: tuple[DistributedLockLease, ...]
    expired_workers: tuple[WorkerRecord, ...]
    snapshot: DistributedRuntimeSnapshot
    health: DistributedHealthReport


@dataclass(frozen=True, slots=True)
class DistributedCancellationResult:
    cancelled_work_item: WorkItem
    snapshot: DistributedRuntimeSnapshot
    health: DistributedHealthReport


@dataclass(frozen=True, slots=True)
class DistributedSchedulingResult:
    scheduled_work_item: WorkItem
    snapshot: DistributedRuntimeSnapshot
    health: DistributedHealthReport


@dataclass(frozen=True, slots=True)
class DistributedWorkerLifecycleResult:
    worker: WorkerRecord
    snapshot: DistributedRuntimeSnapshot
    health: DistributedHealthReport


@dataclass(frozen=True, slots=True)
class DistributedLockLifecycleResult:
    lock: DistributedLockLease
    snapshot: DistributedRuntimeSnapshot
    health: DistributedHealthReport


class DistributedRuntimeCoordinator:
    """Local P6 coordination boundary over queue, scheduler, locks and workers."""

    def __init__(
        self,
        *,
        queue: InMemoryWorkQueue | None = None,
        locks: InMemoryDistributedLockRegistry | None = None,
        workers: InMemoryWorkerRegistry | None = None,
    ) -> None:
        self._queue = queue or InMemoryWorkQueue()
        self._locks = locks or InMemoryDistributedLockRegistry()
        self._workers = workers or InMemoryWorkerRegistry()
        self._scheduler = WorkScheduler(self._queue)

    @property
    def queue(self) -> InMemoryWorkQueue:
        return self._queue

    @property
    def scheduler(self) -> WorkScheduler:
        return self._scheduler

    @property
    def locks(self) -> InMemoryDistributedLockRegistry:
        return self._locks

    @property
    def workers(self) -> InMemoryWorkerRegistry:
        return self._workers

    def snapshot(self) -> DistributedRuntimeSnapshot:
        return build_distributed_runtime_snapshot(
            queue=self._queue,
            locks=self._locks,
            workers=self._workers,
        )

    def health(
        self,
        *,
        now: datetime | None = None,
        queued_backlog_warn_threshold: int = 100,
        lease_expiry_warn_seconds: float = 10.0,
        min_online_workers: int = 1,
    ) -> DistributedHealthReport:
        return build_distributed_health_report(
            self.snapshot(),
            now=now or utc_now(),
            queued_backlog_warn_threshold=queued_backlog_warn_threshold,
            lease_expiry_warn_seconds=lease_expiry_warn_seconds,
            min_online_workers=min_online_workers,
        )

    def schedule_session(
        self,
        session_id: SessionId,
        *,
        payload: JsonMapping | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        available_at: datetime | None = None,
        now: datetime | None = None,
        queued_backlog_warn_threshold: int = 100,
        lease_expiry_warn_seconds: float = 10.0,
        min_online_workers: int = 1,
    ) -> DistributedSchedulingResult:
        scheduled = self._scheduler.schedule_session(
            session_id,
            payload=payload,
            priority=priority,
            max_attempts=max_attempts,
            available_at=available_at,
        )
        timestamp = now or utc_now()
        snapshot = self.snapshot()
        health = build_distributed_health_report(
            snapshot,
            now=timestamp,
            queued_backlog_warn_threshold=queued_backlog_warn_threshold,
            lease_expiry_warn_seconds=lease_expiry_warn_seconds,
            min_online_workers=min_online_workers,
        )
        return DistributedSchedulingResult(
            scheduled_work_item=scheduled,
            snapshot=snapshot,
            health=health,
        )

    def register_worker(
        self,
        worker_id: WorkerId,
        *,
        capabilities: tuple[str, ...] = (),
        metadata: JsonMapping | None = None,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
        queued_backlog_warn_threshold: int = 100,
        lease_expiry_warn_seconds: float = 10.0,
        min_online_workers: int = 1,
    ) -> DistributedWorkerLifecycleResult:
        timestamp = now or utc_now()
        worker = self._workers.register(
            worker_id,
            capabilities=capabilities,
            metadata=metadata,
            ttl_seconds=ttl_seconds,
            now=timestamp,
        )
        snapshot = self.snapshot()
        health = build_distributed_health_report(
            snapshot,
            now=timestamp,
            queued_backlog_warn_threshold=queued_backlog_warn_threshold,
            lease_expiry_warn_seconds=lease_expiry_warn_seconds,
            min_online_workers=min_online_workers,
        )
        return DistributedWorkerLifecycleResult(worker=worker, snapshot=snapshot, health=health)

    def heartbeat_worker(
        self,
        worker_id: WorkerId,
        *,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
        queued_backlog_warn_threshold: int = 100,
        lease_expiry_warn_seconds: float = 10.0,
        min_online_workers: int = 1,
    ) -> DistributedWorkerLifecycleResult:
        timestamp = now or utc_now()
        worker = self._workers.heartbeat(
            worker_id,
            ttl_seconds=ttl_seconds,
            now=timestamp,
        )
        snapshot = self.snapshot()
        health = build_distributed_health_report(
            snapshot,
            now=timestamp,
            queued_backlog_warn_threshold=queued_backlog_warn_threshold,
            lease_expiry_warn_seconds=lease_expiry_warn_seconds,
            min_online_workers=min_online_workers,
        )
        return DistributedWorkerLifecycleResult(worker=worker, snapshot=snapshot, health=health)

    def drain_worker(
        self,
        worker_id: WorkerId,
        *,
        reason: str = "worker draining",
        now: datetime | None = None,
        queued_backlog_warn_threshold: int = 100,
        lease_expiry_warn_seconds: float = 10.0,
        min_online_workers: int = 1,
    ) -> DistributedWorkerLifecycleResult:
        timestamp = now or utc_now()
        worker = self._workers.drain(worker_id, reason=reason, now=timestamp)
        snapshot = self.snapshot()
        health = build_distributed_health_report(
            snapshot,
            now=timestamp,
            queued_backlog_warn_threshold=queued_backlog_warn_threshold,
            lease_expiry_warn_seconds=lease_expiry_warn_seconds,
            min_online_workers=min_online_workers,
        )
        return DistributedWorkerLifecycleResult(worker=worker, snapshot=snapshot, health=health)

    def mark_worker_offline(
        self,
        worker_id: WorkerId,
        *,
        reason: str = "worker offline",
        now: datetime | None = None,
        queued_backlog_warn_threshold: int = 100,
        lease_expiry_warn_seconds: float = 10.0,
        min_online_workers: int = 1,
    ) -> DistributedWorkerLifecycleResult:
        timestamp = now or utc_now()
        worker = self._workers.mark_offline(worker_id, reason=reason, now=timestamp)
        snapshot = self.snapshot()
        health = build_distributed_health_report(
            snapshot,
            now=timestamp,
            queued_backlog_warn_threshold=queued_backlog_warn_threshold,
            lease_expiry_warn_seconds=lease_expiry_warn_seconds,
            min_online_workers=min_online_workers,
        )
        return DistributedWorkerLifecycleResult(worker=worker, snapshot=snapshot, health=health)

    def acquire_lock(
        self,
        *,
        lock_key: str,
        owner_id: DistributedLockOwnerId,
        ttl_seconds: float = 30.0,
        metadata: JsonMapping | None = None,
        now: datetime | None = None,
        queued_backlog_warn_threshold: int = 100,
        lease_expiry_warn_seconds: float = 10.0,
        min_online_workers: int = 1,
    ) -> DistributedLockLifecycleResult:
        timestamp = now or utc_now()
        lock = self._locks.acquire(
            lock_key=lock_key,
            owner_id=owner_id,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
            now=timestamp,
        )
        snapshot = self.snapshot()
        health = build_distributed_health_report(
            snapshot,
            now=timestamp,
            queued_backlog_warn_threshold=queued_backlog_warn_threshold,
            lease_expiry_warn_seconds=lease_expiry_warn_seconds,
            min_online_workers=min_online_workers,
        )
        return DistributedLockLifecycleResult(lock=lock, snapshot=snapshot, health=health)

    def heartbeat_lock(
        self,
        lease_id: DistributedLockLeaseId,
        *,
        owner_id: DistributedLockOwnerId,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
        queued_backlog_warn_threshold: int = 100,
        lease_expiry_warn_seconds: float = 10.0,
        min_online_workers: int = 1,
    ) -> DistributedLockLifecycleResult:
        timestamp = now or utc_now()
        lock = self._locks.heartbeat(
            lease_id,
            owner_id=owner_id,
            ttl_seconds=ttl_seconds,
            now=timestamp,
        )
        snapshot = self.snapshot()
        health = build_distributed_health_report(
            snapshot,
            now=timestamp,
            queued_backlog_warn_threshold=queued_backlog_warn_threshold,
            lease_expiry_warn_seconds=lease_expiry_warn_seconds,
            min_online_workers=min_online_workers,
        )
        return DistributedLockLifecycleResult(lock=lock, snapshot=snapshot, health=health)

    def release_lock(
        self,
        lease_id: DistributedLockLeaseId,
        *,
        owner_id: DistributedLockOwnerId,
        now: datetime | None = None,
        queued_backlog_warn_threshold: int = 100,
        lease_expiry_warn_seconds: float = 10.0,
        min_online_workers: int = 1,
    ) -> DistributedLockLifecycleResult:
        timestamp = now or utc_now()
        lock = self._locks.release(lease_id, owner_id=owner_id, now=timestamp)
        snapshot = self.snapshot()
        health = build_distributed_health_report(
            snapshot,
            now=timestamp,
            queued_backlog_warn_threshold=queued_backlog_warn_threshold,
            lease_expiry_warn_seconds=lease_expiry_warn_seconds,
            min_online_workers=min_online_workers,
        )
        return DistributedLockLifecycleResult(lock=lock, snapshot=snapshot, health=health)

    def expire(
        self,
        *,
        now: datetime | None = None,
        queued_backlog_warn_threshold: int = 100,
        lease_expiry_warn_seconds: float = 10.0,
        min_online_workers: int = 1,
    ) -> DistributedMaintenanceResult:
        timestamp = now or utc_now()
        expired_work_items = self._queue.expire(now=timestamp)
        expired_locks = self._locks.expire(now=timestamp)
        expired_workers = self._workers.expire(now=timestamp)
        snapshot = self.snapshot()
        health = build_distributed_health_report(
            snapshot,
            now=timestamp,
            queued_backlog_warn_threshold=queued_backlog_warn_threshold,
            lease_expiry_warn_seconds=lease_expiry_warn_seconds,
            min_online_workers=min_online_workers,
        )
        return DistributedMaintenanceResult(
            ran_at=timestamp,
            expired_work_items=expired_work_items,
            expired_locks=expired_locks,
            expired_workers=expired_workers,
            snapshot=snapshot,
            health=health,
        )

    def cancel_work_item(
        self,
        work_item_id: WorkItemId,
        *,
        reason: str = "distributed work item cancelled",
        now: datetime | None = None,
        queued_backlog_warn_threshold: int = 100,
        lease_expiry_warn_seconds: float = 10.0,
        min_online_workers: int = 1,
    ) -> DistributedCancellationResult:
        timestamp = now or utc_now()
        cancelled = self._queue.cancel(work_item_id, reason=reason, now=timestamp)
        snapshot = self.snapshot()
        health = build_distributed_health_report(
            snapshot,
            now=timestamp,
            queued_backlog_warn_threshold=queued_backlog_warn_threshold,
            lease_expiry_warn_seconds=lease_expiry_warn_seconds,
            min_online_workers=min_online_workers,
        )
        return DistributedCancellationResult(
            cancelled_work_item=cancelled,
            snapshot=snapshot,
            health=health,
        )
