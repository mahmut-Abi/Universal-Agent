from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from inspect import isawaitable

from universal_agent.core import utc_now
from universal_agent.core.config_validation import (
    parse_non_empty_string,
    parse_optional_positive_float,
    parse_positive_float,
    parse_positive_int,
)
from universal_agent.distributed.queue import (
    InMemoryWorkQueue,
    LeaseId,
    LeaseLostError,
    NoWorkAvailable,
    WorkerId,
    WorkItem,
    WorkItemStatus,
)
from universal_agent.distributed.worker_state import (
    InMemoryWorkerRegistry,
    WorkerNotFoundError,
    WorkerStatus,
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
    WORKER_INACTIVE = "worker_inactive"


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
        if self.status is not WorkHandlerStatus.COMPLETED:
            parse_non_empty_string(
                self.reason,
                "non-completed handler result",
                empty_template="{path} requires a reason",
            )


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
        worker_registry: InMemoryWorkerRegistry | None = None,
        worker_ttl_seconds: float = 30.0,
        worker_capabilities: tuple[str, ...] | None = None,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        parse_non_empty_string(str(worker_id), "worker_id")
        parse_positive_float(lease_ttl_seconds, "lease_ttl_seconds")
        parse_positive_float(worker_ttl_seconds, "worker_ttl_seconds")
        parse_optional_positive_float(heartbeat_interval_seconds, "heartbeat_interval_seconds")
        self._queue = queue
        self._worker_id = worker_id
        self._handlers = dict(handlers)
        self._lease_ttl_seconds = lease_ttl_seconds
        self._worker_registry = worker_registry
        self._worker_ttl_seconds = worker_ttl_seconds
        self._worker_capabilities = (
            tuple(sorted(self._handlers)) if worker_capabilities is None else worker_capabilities
        )
        self._heartbeat_interval_seconds = heartbeat_interval_seconds or (lease_ttl_seconds / 2)

    async def run_once(self) -> WorkerRunResult:
        now = utc_now()
        inactive = self._prepare_worker(now=now)
        if inactive is not None:
            return inactive
        try:
            item = self._queue.lease(
                worker_id=self._worker_id,
                ttl_seconds=self._lease_ttl_seconds,
                now=now,
                accepted_kinds=self._worker_capabilities,
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
            result = await self._call_handler(handler, item, lease.lease_id)
        except LeaseLostError as exc:
            return WorkerRunResult(
                status=WorkerRunStatus.LEASE_LOST,
                worker_id=self._worker_id,
                work_item=self._queue.get(item.work_item_id),
                lease_id=lease.lease_id,
                reason=str(exc),
            )
        except Exception as exc:
            return self._fail_leased_item(
                item,
                lease.lease_id,
                reason=f"handler failed: {exc}",
                retry=True,
            )
        return self._apply_handler_result(item, lease.lease_id, result)

    async def run_until_idle(self, *, max_items: int) -> tuple[WorkerRunResult, ...]:
        parse_positive_int(max_items, "max_items")
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

    def _prepare_worker(self, *, now: datetime) -> WorkerRunResult | None:
        if self._worker_registry is None:
            return None
        try:
            record = self._worker_registry.get(self._worker_id)
        except WorkerNotFoundError:
            self._worker_registry.register(
                self._worker_id,
                capabilities=self._worker_capabilities,
                ttl_seconds=self._worker_ttl_seconds,
                now=now,
            )
            return None
        if record.status in {WorkerStatus.DRAINING, WorkerStatus.OFFLINE, WorkerStatus.LOST}:
            return WorkerRunResult(
                status=WorkerRunStatus.WORKER_INACTIVE,
                worker_id=self._worker_id,
                reason=f"worker is {record.status.value}",
            )
        try:
            self._worker_registry.heartbeat(
                self._worker_id,
                ttl_seconds=self._worker_ttl_seconds,
                now=now,
            )
        except WorkerNotFoundError as exc:
            return WorkerRunResult(
                status=WorkerRunStatus.WORKER_INACTIVE,
                worker_id=self._worker_id,
                reason=str(exc),
            )
        return None

    async def _call_handler(
        self,
        handler: WorkHandler,
        item: WorkItem,
        lease_id: LeaseId,
    ) -> WorkHandlerResult:
        result = handler(item)
        if not isawaitable(result):
            return result
        task = asyncio.ensure_future(result)
        try:
            while True:
                done, _ = await asyncio.wait(
                    {task},
                    timeout=self._heartbeat_interval_seconds,
                )
                if task in done:
                    return task.result()
                self._heartbeat_active_lease(lease_id)
        except BaseException:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            raise

    def _heartbeat_active_lease(self, lease_id: LeaseId) -> None:
        now = utc_now()
        self._queue.heartbeat(
            lease_id,
            worker_id=self._worker_id,
            ttl_seconds=self._lease_ttl_seconds,
            now=now,
        )
        if self._worker_registry is None:
            return
        try:
            self._worker_registry.heartbeat(
                self._worker_id,
                ttl_seconds=self._worker_ttl_seconds,
                now=now,
            )
        except WorkerNotFoundError as exc:
            with suppress(LeaseLostError):
                self._queue.fail(
                    lease_id,
                    worker_id=self._worker_id,
                    reason=f"worker heartbeat failed: {exc}",
                    retry=True,
                    now=now,
                )
            raise LeaseLostError(f"worker heartbeat failed: {exc}") from exc

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
