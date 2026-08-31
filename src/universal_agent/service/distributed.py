from __future__ import annotations

from datetime import datetime

from universal_agent.core import ActionId, Goal, JsonMapping, SessionId, Task, TaskId
from universal_agent.distributed import (
    DistributedCancellationResult,
    DistributedHealthReport,
    DistributedLockLeaseId,
    DistributedLockLifecycleResult,
    DistributedLockOwnerId,
    DistributedMaintenanceResult,
    DistributedPruneResult,
    DistributedRuntimeSnapshot,
    DistributedSchedulingResult,
    DistributedWorkerLifecycleResult,
    WorkerId,
    WorkerRunResult,
    WorkItemId,
)
from universal_agent.service.distributed_runtime import DistributedRuntimeController
from universal_agent.service.views import DistributedPendingActionSchedulingResult


class DistributedService:
    """Thin delegation over the distributed runtime controller.

    `RuntimeService` owns the controller; this object keeps the scheduling,
    worker, lock and maintenance surface off the main service class so the
    distributed concerns stay in one place.
    """

    def __init__(self, controller: DistributedRuntimeController) -> None:
        self._controller = controller

    def snapshot(self) -> DistributedRuntimeSnapshot | None:
        return self._controller.snapshot()

    def health(self, *, now: datetime | None = None) -> DistributedHealthReport | None:
        return self._controller.health(now=now)

    def schedule_session(
        self,
        session_id: SessionId,
        *,
        payload: JsonMapping | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> DistributedSchedulingResult | None:
        return self._controller.schedule_session(
            session_id,
            payload=payload,
            priority=priority,
            max_attempts=max_attempts,
            now=now,
        )

    def schedule_goal(
        self,
        goal: Goal,
        task: Task,
        *,
        priority: int = 0,
        max_attempts: int = 3,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> DistributedSchedulingResult | None:
        return self._controller.schedule_goal(
            goal,
            task,
            priority=priority,
            max_attempts=max_attempts,
            idempotency_key=idempotency_key,
            now=now,
        )

    def schedule_task(
        self,
        session_id: SessionId,
        task_id: TaskId,
        *,
        payload: JsonMapping | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> DistributedSchedulingResult | None:
        return self._controller.schedule_task(
            session_id,
            task_id,
            payload=payload,
            priority=priority,
            max_attempts=max_attempts,
            now=now,
        )

    def schedule_action(
        self,
        session_id: SessionId,
        task_id: TaskId,
        action_id: ActionId,
        *,
        confirmed: bool,
        priority: int = 0,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> DistributedSchedulingResult | None:
        return self._controller.schedule_action(
            session_id,
            task_id,
            action_id,
            confirmed=confirmed,
            priority=priority,
            max_attempts=max_attempts,
            now=now,
        )

    async def schedule_pending_actions(
        self,
        *,
        confirmed: bool,
        priority: int = 0,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> DistributedPendingActionSchedulingResult | None:
        return await self._controller.schedule_pending_actions(
            confirmed=confirmed,
            priority=priority,
            max_attempts=max_attempts,
            now=now,
        )

    def register_worker(
        self,
        worker_id: WorkerId,
        *,
        capabilities: tuple[str, ...] = (),
        metadata: JsonMapping | None = None,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> DistributedWorkerLifecycleResult | None:
        return self._controller.register_worker(
            worker_id,
            capabilities=capabilities,
            metadata=metadata,
            ttl_seconds=ttl_seconds,
            now=now,
        )

    def heartbeat_worker(
        self,
        worker_id: WorkerId,
        *,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> DistributedWorkerLifecycleResult | None:
        return self._controller.heartbeat_worker(
            worker_id,
            ttl_seconds=ttl_seconds,
            now=now,
        )

    def drain_worker(
        self,
        worker_id: WorkerId,
        *,
        reason: str = "worker draining",
        now: datetime | None = None,
    ) -> DistributedWorkerLifecycleResult | None:
        return self._controller.drain_worker(
            worker_id,
            reason=reason,
            now=now,
        )

    def mark_worker_offline(
        self,
        worker_id: WorkerId,
        *,
        reason: str = "worker offline",
        now: datetime | None = None,
    ) -> DistributedWorkerLifecycleResult | None:
        return self._controller.mark_worker_offline(
            worker_id,
            reason=reason,
            now=now,
        )

    def acquire_lock(
        self,
        *,
        lock_key: str,
        owner_id: DistributedLockOwnerId,
        ttl_seconds: float = 30.0,
        metadata: JsonMapping | None = None,
        now: datetime | None = None,
    ) -> DistributedLockLifecycleResult | None:
        return self._controller.acquire_lock(
            lock_key=lock_key,
            owner_id=owner_id,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
            now=now,
        )

    def heartbeat_lock(
        self,
        lease_id: DistributedLockLeaseId,
        *,
        owner_id: DistributedLockOwnerId,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> DistributedLockLifecycleResult | None:
        return self._controller.heartbeat_lock(
            lease_id,
            owner_id=owner_id,
            ttl_seconds=ttl_seconds,
            now=now,
        )

    def release_lock(
        self,
        lease_id: DistributedLockLeaseId,
        *,
        owner_id: DistributedLockOwnerId,
        now: datetime | None = None,
    ) -> DistributedLockLifecycleResult | None:
        return self._controller.release_lock(
            lease_id,
            owner_id=owner_id,
            now=now,
        )

    def expire(
        self,
        *,
        now: datetime | None = None,
    ) -> DistributedMaintenanceResult | None:
        return self._controller.expire(now=now)

    def prune_terminal_work_items(
        self,
        *,
        before: datetime | None = None,
        now: datetime | None = None,
    ) -> DistributedPruneResult | None:
        return self._controller.prune_terminal_work_items(before=before, now=now)

    def cancel_work_item(
        self,
        work_item_id: WorkItemId,
        *,
        reason: str = "distributed work item cancelled",
        now: datetime | None = None,
    ) -> DistributedCancellationResult | None:
        return self._controller.cancel_work_item(
            work_item_id,
            reason=reason,
            now=now,
        )

    async def run_worker_once(
        self,
        worker_id: WorkerId,
        *,
        lease_ttl_seconds: float = 30.0,
        worker_ttl_seconds: float = 30.0,
        heartbeat_interval_seconds: float | None = None,
    ) -> WorkerRunResult | None:
        return await self._controller.run_worker_once(
            worker_id,
            lease_ttl_seconds=lease_ttl_seconds,
            worker_ttl_seconds=worker_ttl_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )

    async def run_worker_until_idle(
        self,
        worker_id: WorkerId,
        *,
        max_items: int,
        lease_ttl_seconds: float = 30.0,
        worker_ttl_seconds: float = 30.0,
        heartbeat_interval_seconds: float | None = None,
    ) -> tuple[WorkerRunResult, ...] | None:
        return await self._controller.run_worker_until_idle(
            worker_id,
            max_items=max_items,
            lease_ttl_seconds=lease_ttl_seconds,
            worker_ttl_seconds=worker_ttl_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )
