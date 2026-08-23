from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from inspect import isawaitable

from universal_agent.core import utc_now
from universal_agent.distributed.queue import (
    InMemoryWorkQueue,
    LeaseId,
    LeaseLostError,
    NoWorkAvailable,
    WorkerId,
    WorkItem,
    WorkItemStatus,
)


class WorkHandlerStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkerRunStatus(StrEnum):
    COMPLETED = "completed"
    RETRYING = "retrying"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NO_WORK = "no_work"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True, slots=True)
class WorkHandlerResult:
    status: WorkHandlerStatus
    reason: str = ""
    retry: bool = True

    @classmethod
    def completed(cls, reason: str = "completed") -> WorkHandlerResult:
        return cls(WorkHandlerStatus.COMPLETED, reason=reason, retry=False)

    @classmethod
    def failed(cls, reason: str, *, retry: bool = True) -> WorkHandlerResult:
        return cls(WorkHandlerStatus.FAILED, reason=reason, retry=retry)

    @classmethod
    def cancelled(cls, reason: str = "cancelled") -> WorkHandlerResult:
        return cls(WorkHandlerStatus.CANCELLED, reason=reason, retry=False)

    def __post_init__(self) -> None:
        if self.status is not WorkHandlerStatus.COMPLETED and not self.reason.strip():
            raise ValueError("non-completed handler result requires a reason")


WorkHandler = Callable[[WorkItem], WorkHandlerResult | Awaitable[WorkHandlerResult]]


@dataclass(frozen=True, slots=True)
class WorkerRunResult:
    status: WorkerRunStatus
    worker_id: WorkerId
    work_item: WorkItem | None = None
    lease_id: LeaseId | None = None
    reason: str = ""


class WorkQueueWorker:
    """Local P6 worker adapter over the typed queue/lease primitive."""

    def __init__(
        self,
        *,
        queue: InMemoryWorkQueue,
        worker_id: WorkerId,
        handlers: Mapping[str, WorkHandler],
        lease_ttl_seconds: float = 30.0,
    ) -> None:
        if not str(worker_id).strip():
            raise ValueError("worker_id must not be empty")
        if lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be positive")
        self._queue = queue
        self._worker_id = worker_id
        self._handlers = dict(handlers)
        self._lease_ttl_seconds = lease_ttl_seconds

    async def run_once(self) -> WorkerRunResult:
        now = utc_now()
        try:
            item = self._queue.lease(
                worker_id=self._worker_id,
                ttl_seconds=self._lease_ttl_seconds,
                now=now,
            )
        except NoWorkAvailable:
            return WorkerRunResult(
                status=WorkerRunStatus.NO_WORK,
                worker_id=self._worker_id,
                reason="no work available",
            )
        lease = item.lease
        if lease is None:
            return WorkerRunResult(
                status=WorkerRunStatus.LEASE_LOST,
                worker_id=self._worker_id,
                work_item=item,
                reason="leased work item did not include lease metadata",
            )
        handler = self._handlers.get(item.kind)
        if handler is None:
            return self._fail_leased_item(
                item,
                lease.lease_id,
                reason=f"no handler registered for work kind: {item.kind}",
                retry=False,
            )
        try:
            result = await _call_handler(handler, item)
        except Exception as exc:
            return self._fail_leased_item(
                item,
                lease.lease_id,
                reason=f"handler failed: {exc}",
                retry=True,
            )
        return self._apply_handler_result(item, lease.lease_id, result)

    async def run_until_idle(self, *, max_items: int) -> tuple[WorkerRunResult, ...]:
        if max_items < 1:
            raise ValueError("max_items must be positive")
        results: list[WorkerRunResult] = []
        for _ in range(max_items):
            result = await self.run_once()
            results.append(result)
            if result.status is WorkerRunStatus.NO_WORK:
                break
        return tuple(results)

    def _apply_handler_result(
        self,
        item: WorkItem,
        lease_id: LeaseId,
        result: WorkHandlerResult,
    ) -> WorkerRunResult:
        try:
            if result.status is WorkHandlerStatus.COMPLETED:
                completed = self._queue.complete(
                    lease_id,
                    worker_id=self._worker_id,
                    now=utc_now(),
                )
                return WorkerRunResult(
                    status=WorkerRunStatus.COMPLETED,
                    worker_id=self._worker_id,
                    work_item=completed,
                    lease_id=lease_id,
                    reason=result.reason,
                )
            if result.status is WorkHandlerStatus.CANCELLED:
                cancelled = self._queue.cancel(
                    item.work_item_id,
                    reason=result.reason,
                    now=utc_now(),
                )
                return WorkerRunResult(
                    status=WorkerRunStatus.CANCELLED,
                    worker_id=self._worker_id,
                    work_item=cancelled,
                    lease_id=lease_id,
                    reason=result.reason,
                )
            failed = self._queue.fail(
                lease_id,
                worker_id=self._worker_id,
                reason=result.reason,
                retry=result.retry,
                now=utc_now(),
            )
        except LeaseLostError as exc:
            return WorkerRunResult(
                status=WorkerRunStatus.LEASE_LOST,
                worker_id=self._worker_id,
                work_item=self._queue.get(item.work_item_id),
                lease_id=lease_id,
                reason=str(exc),
            )
        return _worker_result_from_failed_item(self._worker_id, lease_id, failed)

    def _fail_leased_item(
        self,
        item: WorkItem,
        lease_id: LeaseId,
        *,
        reason: str,
        retry: bool,
    ) -> WorkerRunResult:
        try:
            failed = self._queue.fail(
                lease_id,
                worker_id=self._worker_id,
                reason=reason,
                retry=retry,
                now=utc_now(),
            )
        except LeaseLostError as exc:
            return WorkerRunResult(
                status=WorkerRunStatus.LEASE_LOST,
                worker_id=self._worker_id,
                work_item=self._queue.get(item.work_item_id),
                lease_id=lease_id,
                reason=str(exc),
            )
        return _worker_result_from_failed_item(self._worker_id, lease_id, failed)


async def _call_handler(handler: WorkHandler, item: WorkItem) -> WorkHandlerResult:
    result = handler(item)
    if isawaitable(result):
        return await result
    return result


def _worker_result_from_failed_item(
    worker_id: WorkerId,
    lease_id: LeaseId,
    item: WorkItem,
) -> WorkerRunResult:
    return WorkerRunResult(
        status=(
            WorkerRunStatus.RETRYING
            if item.status is WorkItemStatus.QUEUED
            else WorkerRunStatus.FAILED
        ),
        worker_id=worker_id,
        work_item=item,
        lease_id=lease_id,
        reason=item.last_error or "",
    )
