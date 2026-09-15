from __future__ import annotations

from collections.abc import Collection
from dataclasses import replace
from datetime import datetime, timedelta

from universal_agent.core import (
    ActionId,
    JsonMapping,
    SessionId,
    TaskId,
    immutable_json,
    utc_now,
)
from universal_agent.core.config_validation import (
    parse_non_empty_string,
    parse_non_empty_string_sequence,
    parse_positive_float,
)
from universal_agent.distributed.queue_models import (
    FencingToken,
    LeaseId,
    LeaseLostError,
    NoWorkAvailable,
    WorkerId,
    WorkerLease,
    WorkItem,
    WorkItemId,
    WorkItemNotFoundError,
    WorkItemStatus,
)


class InMemoryWorkQueue:
    """P6 local queue/lease/heartbeat primitive for distributed runtime adapters."""

    def __init__(self) -> None:
        self._items: dict[WorkItemId, WorkItem] = {}
        self._sequence = 0
        self._fencing_sequences: dict[WorkItemId, int] = {}

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
        accepted_kinds: Collection[str] | None = None,
    ) -> WorkItem:
        timestamp = now or utc_now()
        kind_filter = _normalize_accepted_kinds(accepted_kinds)
        self.expire(now=timestamp)
        item = self._next_leaseable(timestamp, accepted_kinds=kind_filter)
        if item is None:
            raise NoWorkAvailable("no work available")
        lease = WorkerLease(
            lease_id=self._next_lease_id(item),
            worker_id=worker_id,
            leased_at=timestamp,
            lease_expires_at=_lease_deadline(timestamp, ttl_seconds),
            heartbeat_at=timestamp,
            fencing_token=FencingToken(self._next_fencing_token(item.work_item_id)),
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
        item = self._leased_item(lease_id, worker_id, now=timestamp)
        lease = item.lease
        if lease is None:
            raise LeaseLostError(f"lease not found: {lease_id}")
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
        item = self._leased_item(lease_id, worker_id, now=timestamp)
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
        parse_non_empty_string(reason, "failure reason")
        timestamp = now or utc_now()
        item = self._leased_item(lease_id, worker_id, now=timestamp)
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
        parse_non_empty_string(reason, "cancellation reason")
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

    def prune_terminal(self, *, before: datetime | None = None) -> tuple[WorkItem, ...]:
        """Remove terminal work items, optionally only items completed before a timestamp."""

        pruned = tuple(
            sorted(
                (
                    item
                    for item in self._items.values()
                    if _is_prunable_terminal(item, before=before)
                ),
                key=_sort_key,
            )
        )
        for item in pruned:
            del self._items[item.work_item_id]
            self._fencing_sequences.pop(item.work_item_id, None)
        return pruned

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
            if item.idempotency_key == idempotency_key and not _is_terminal(item):
                return item
        return None

    def _next_fencing_token(self, work_item_id: WorkItemId) -> int:
        """Return the next monotonic fencing token for one work item."""

        next_token = self._fencing_sequences.get(work_item_id, 0) + 1
        self._fencing_sequences[work_item_id] = next_token
        return next_token

    def _restore_fencing_sequence(self, work_item_id: WorkItemId, token: int) -> None:
        """Keep the highest seen fencing token when reloading persisted items."""

        if token > self._fencing_sequences.get(work_item_id, 0):
            self._fencing_sequences[work_item_id] = token

    def _next_leaseable(
        self,
        now: datetime,
        *,
        accepted_kinds: frozenset[str] | None = None,
    ) -> WorkItem | None:
        candidates = tuple(
            item
            for item in self._items.values()
            if item.status is WorkItemStatus.QUEUED
            and item.available_at <= now
            and (accepted_kinds is None or item.kind in accepted_kinds)
        )
        if not candidates:
            return None
        return sorted(candidates, key=_sort_key)[0]

    def _leased_item(
        self,
        lease_id: LeaseId,
        worker_id: WorkerId,
        *,
        now: datetime | None = None,
    ) -> WorkItem:
        found: WorkItem | None = None
        for item in self._items.values():
            lease = item.lease
            if (
                item.status is WorkItemStatus.LEASED
                and lease is not None
                and lease.lease_id == lease_id
            ):
                if lease.worker_id != worker_id:
                    raise LeaseLostError(f"lease is owned by another worker: {lease_id}")
                found = item
                break
        if found is None:
            raise LeaseLostError(f"lease not found: {lease_id}")
        lease = found.lease
        if lease is not None and now is not None and lease.lease_expires_at <= now:
            self.expire(now=now)
            raise LeaseLostError(f"lease expired: {lease_id}")
        return found

    def _next_work_item_id(self) -> WorkItemId:
        self._sequence += 1
        return WorkItemId(f"work-{self._sequence}")

    def _next_lease_id(self, item: WorkItem) -> LeaseId:
        return LeaseId(f"lease-{item.work_item_id}-{item.attempts + 1}")


def _lease_deadline(now: datetime, ttl_seconds: float) -> datetime:
    parse_positive_float(ttl_seconds, "ttl_seconds")
    return now + timedelta(seconds=ttl_seconds)


def _normalize_accepted_kinds(accepted_kinds: Collection[str] | None) -> frozenset[str] | None:
    if accepted_kinds is None:
        return None
    validated = parse_non_empty_string_sequence(
        tuple(accepted_kinds),
        "accepted_kinds",
        empty_template="accepted_kinds must not include empty kinds",
        item_type_template="accepted_kinds must not include empty kinds",
    )
    return frozenset(kind.strip() for kind in validated)


def _sort_key(item: WorkItem) -> tuple[int, datetime, str]:
    return (-item.priority, item.available_at, str(item.work_item_id))


def _is_terminal(item: WorkItem) -> bool:
    return item.status in {
        WorkItemStatus.COMPLETED,
        WorkItemStatus.FAILED,
        WorkItemStatus.CANCELLED,
    }


def _is_prunable_terminal(item: WorkItem, *, before: datetime | None) -> bool:
    if not _is_terminal(item):
        return False
    if before is None:
        return True
    terminal_at = _terminal_at(item)
    return terminal_at is not None and terminal_at <= before


def _terminal_at(item: WorkItem) -> datetime | None:
    if item.status is WorkItemStatus.COMPLETED:
        return item.completed_at
    if item.status is WorkItemStatus.FAILED:
        return item.failed_at
    if item.status is WorkItemStatus.CANCELLED:
        return item.cancelled_at
    return None


def _sequence_from_work_item_id(work_item_id: WorkItemId) -> int:
    value = str(work_item_id)
    if not value.startswith("work-"):
        return 0
    suffix = value.removeprefix("work-")
    if not suffix.isdecimal():
        return 0
    return int(suffix)
