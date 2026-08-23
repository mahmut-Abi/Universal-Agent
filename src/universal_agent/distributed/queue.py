from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import NewType

from universal_agent.core import (
    ActionId,
    JsonMapping,
    SessionId,
    TaskId,
    immutable_json,
    utc_now,
)

WorkItemId = NewType("WorkItemId", str)
WorkerId = NewType("WorkerId", str)
LeaseId = NewType("LeaseId", str)


class WorkItemStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NoWorkAvailable(LookupError):
    pass


class WorkItemNotFoundError(LookupError):
    pass


class LeaseLostError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WorkerLease:
    lease_id: LeaseId
    worker_id: WorkerId
    leased_at: datetime
    lease_expires_at: datetime
    heartbeat_at: datetime


@dataclass(frozen=True, slots=True)
class WorkItem:
    work_item_id: WorkItemId
    kind: str
    payload: JsonMapping = field(default_factory=immutable_json)
    session_id: SessionId | None = None
    task_id: TaskId | None = None
    action_id: ActionId | None = None
    priority: int = 0
    max_attempts: int = 3
    attempts: int = 0
    status: WorkItemStatus = WorkItemStatus.QUEUED
    created_at: datetime = field(default_factory=utc_now)
    available_at: datetime = field(default_factory=utc_now)
    lease: WorkerLease | None = None
    idempotency_key: str | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    cancelled_at: datetime | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        if not str(self.work_item_id).strip():
            raise ValueError("work_item_id must not be empty")
        if not self.kind.strip():
            raise ValueError("work item kind must not be empty")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.attempts < 0:
            raise ValueError("attempts must be non-negative")
        if self.status is not WorkItemStatus.LEASED and self.lease is not None:
            raise ValueError("only leased work items may carry a lease")


class InMemoryWorkQueue:
    """P6 local queue/lease/heartbeat primitive for distributed runtime adapters."""

    def __init__(self) -> None:
        self._items: dict[WorkItemId, WorkItem] = {}
        self._sequence = 0

    def enqueue(
        self,
        *,
        kind: str,
        payload: JsonMapping | None = None,
        session_id: SessionId | None = None,
        task_id: TaskId | None = None,
        action_id: ActionId | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        available_at: datetime | None = None,
        idempotency_key: str | None = None,
        work_item_id: WorkItemId | None = None,
    ) -> WorkItem:
        if idempotency_key is not None:
            existing = self._find_idempotent(idempotency_key)
            if existing is not None:
                return existing
        item = WorkItem(
            work_item_id=work_item_id or self._next_work_item_id(),
            kind=kind,
            payload=immutable_json(payload),
            session_id=session_id,
            task_id=task_id,
            action_id=action_id,
            priority=priority,
            max_attempts=max_attempts,
            available_at=available_at or utc_now(),
            idempotency_key=idempotency_key,
        )
        if item.work_item_id in self._items:
            raise ValueError(f"work item already exists: {item.work_item_id}")
        self._items[item.work_item_id] = item
        return item

    def lease(
        self,
        *,
        worker_id: WorkerId,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> WorkItem:
        timestamp = now or utc_now()
        self.expire(now=timestamp)
        item = self._next_leaseable(timestamp)
        if item is None:
            raise NoWorkAvailable("no work available")
        lease = WorkerLease(
            lease_id=self._next_lease_id(item),
            worker_id=worker_id,
            leased_at=timestamp,
            lease_expires_at=_lease_deadline(timestamp, ttl_seconds),
            heartbeat_at=timestamp,
        )
        leased = replace(
            item,
            status=WorkItemStatus.LEASED,
            attempts=item.attempts + 1,
            lease=lease,
            last_error=None,
        )
        self._items[item.work_item_id] = leased
        return leased

    def heartbeat(
        self,
        lease_id: LeaseId,
        *,
        worker_id: WorkerId,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> WorkItem:
        timestamp = now or utc_now()
        item = self._leased_item(lease_id, worker_id)
        lease = item.lease
        if lease is None or lease.lease_expires_at <= timestamp:
            self.expire(now=timestamp)
            raise LeaseLostError(f"lease expired: {lease_id}")
        renewed = replace(
            item,
            lease=replace(
                lease,
                heartbeat_at=timestamp,
                lease_expires_at=_lease_deadline(timestamp, ttl_seconds),
            ),
        )
        self._items[item.work_item_id] = renewed
        return renewed

    def complete(
        self,
        lease_id: LeaseId,
        *,
        worker_id: WorkerId,
        now: datetime | None = None,
    ) -> WorkItem:
        timestamp = now or utc_now()
        item = self._leased_item(lease_id, worker_id)
        completed = replace(
            item,
            status=WorkItemStatus.COMPLETED,
            lease=None,
            completed_at=timestamp,
        )
        self._items[item.work_item_id] = completed
        return completed

    def fail(
        self,
        lease_id: LeaseId,
        *,
        worker_id: WorkerId,
        reason: str,
        retry: bool = True,
        now: datetime | None = None,
    ) -> WorkItem:
        if not reason.strip():
            raise ValueError("failure reason must not be empty")
        timestamp = now or utc_now()
        item = self._leased_item(lease_id, worker_id)
        if retry and item.attempts < item.max_attempts:
            failed = replace(
                item,
                status=WorkItemStatus.QUEUED,
                lease=None,
                available_at=timestamp,
                last_error=reason,
            )
        else:
            failed = replace(
                item,
                status=WorkItemStatus.FAILED,
                lease=None,
                failed_at=timestamp,
                last_error=reason,
            )
        self._items[item.work_item_id] = failed
        return failed

    def cancel(
        self,
        work_item_id: WorkItemId,
        *,
        reason: str = "cancelled",
        now: datetime | None = None,
    ) -> WorkItem:
        if not reason.strip():
            raise ValueError("cancellation reason must not be empty")
        item = self.get(work_item_id)
        if item.status in {
            WorkItemStatus.COMPLETED,
            WorkItemStatus.FAILED,
            WorkItemStatus.CANCELLED,
        }:
            return item
        cancelled = replace(
            item,
            status=WorkItemStatus.CANCELLED,
            lease=None,
            cancelled_at=now or utc_now(),
            last_error=reason,
        )
        self._items[work_item_id] = cancelled
        return cancelled

    def expire(self, *, now: datetime | None = None) -> tuple[WorkItem, ...]:
        timestamp = now or utc_now()
        expired: list[WorkItem] = []
        for item in tuple(self._items.values()):
            if item.status is not WorkItemStatus.LEASED or item.lease is None:
                continue
            if item.lease.lease_expires_at > timestamp:
                continue
            if item.attempts < item.max_attempts:
                replacement = replace(
                    item,
                    status=WorkItemStatus.QUEUED,
                    lease=None,
                    available_at=timestamp,
                    last_error=f"lease expired: {item.lease.lease_id}",
                )
            else:
                replacement = replace(
                    item,
                    status=WorkItemStatus.FAILED,
                    lease=None,
                    failed_at=timestamp,
                    last_error=f"lease expired: {item.lease.lease_id}",
                )
            self._items[item.work_item_id] = replacement
            expired.append(replacement)
        return tuple(expired)

    def get(self, work_item_id: WorkItemId) -> WorkItem:
        try:
            return self._items[work_item_id]
        except KeyError as exc:
            raise WorkItemNotFoundError(f"work item not found: {work_item_id}") from exc

    def list(self, *, status: WorkItemStatus | None = None) -> tuple[WorkItem, ...]:
        items = tuple(self._items.values())
        if status is not None:
            items = tuple(item for item in items if item.status is status)
        return tuple(sorted(items, key=_sort_key))

    def queued(self) -> tuple[WorkItem, ...]:
        return self.list(status=WorkItemStatus.QUEUED)

    def leased(self) -> tuple[WorkItem, ...]:
        return self.list(status=WorkItemStatus.LEASED)

    def _find_idempotent(self, idempotency_key: str) -> WorkItem | None:
        for item in self._items.values():
            if item.idempotency_key == idempotency_key:
                return item
        return None

    def _next_leaseable(self, now: datetime) -> WorkItem | None:
        candidates = tuple(
            item
            for item in self._items.values()
            if item.status is WorkItemStatus.QUEUED and item.available_at <= now
        )
        if not candidates:
            return None
        return sorted(candidates, key=_sort_key)[0]

    def _leased_item(self, lease_id: LeaseId, worker_id: WorkerId) -> WorkItem:
        for item in self._items.values():
            lease = item.lease
            if (
                item.status is WorkItemStatus.LEASED
                and lease is not None
                and lease.lease_id == lease_id
            ):
                if lease.worker_id != worker_id:
                    raise LeaseLostError(f"lease is owned by another worker: {lease_id}")
                return item
        raise LeaseLostError(f"lease not found: {lease_id}")

    def _next_work_item_id(self) -> WorkItemId:
        self._sequence += 1
        return WorkItemId(f"work-{self._sequence}")

    def _next_lease_id(self, item: WorkItem) -> LeaseId:
        return LeaseId(f"lease-{item.work_item_id}-{item.attempts + 1}")


def _lease_deadline(now: datetime, ttl_seconds: float) -> datetime:
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    return now + timedelta(seconds=ttl_seconds)


def _sort_key(item: WorkItem) -> tuple[int, datetime, str]:
    return (-item.priority, item.available_at, str(item.work_item_id))
