from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from universal_agent.core import utc_now
from universal_agent.distributed.health import (
    DistributedHealthReport,
    build_distributed_health_report,
)
from universal_agent.distributed.locks import (
    DistributedLockLease,
    InMemoryDistributedLockRegistry,
)
from universal_agent.distributed.queue import InMemoryWorkQueue, WorkItem
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
