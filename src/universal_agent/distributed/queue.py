from __future__ import annotations

import json
from collections.abc import Callable, Collection
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
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
        if not reason.strip():
            raise ValueError("failure reason must not be empty")
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


class FileWorkQueue(InMemoryWorkQueue):
    """File-backed local WorkQueue adapter.

    The queue semantics stay in the in-memory implementation; this adapter adds
    local durability by loading one JSON document at startup and atomically
    replacing it after every mutating operation. It is intentionally a local P6
    primitive, not a cross-process locking or HA queue implementation.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        self._load()

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
        item = super().enqueue(
            kind=kind,
            payload=payload,
            session_id=session_id,
            task_id=task_id,
            action_id=action_id,
            priority=priority,
            max_attempts=max_attempts,
            available_at=available_at,
            idempotency_key=idempotency_key,
            work_item_id=work_item_id,
        )
        self._save()
        return item

    def lease(
        self,
        *,
        worker_id: WorkerId,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
        accepted_kinds: Collection[str] | None = None,
    ) -> WorkItem:
        item = super().lease(
            worker_id=worker_id,
            ttl_seconds=ttl_seconds,
            now=now,
            accepted_kinds=accepted_kinds,
        )
        self._save()
        return item

    def heartbeat(
        self,
        lease_id: LeaseId,
        *,
        worker_id: WorkerId,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> WorkItem:
        item = super().heartbeat(
            lease_id,
            worker_id=worker_id,
            ttl_seconds=ttl_seconds,
            now=now,
        )
        self._save()
        return item

    def complete(
        self,
        lease_id: LeaseId,
        *,
        worker_id: WorkerId,
        now: datetime | None = None,
    ) -> WorkItem:
        item = super().complete(lease_id, worker_id=worker_id, now=now)
        self._save()
        return item

    def fail(
        self,
        lease_id: LeaseId,
        *,
        worker_id: WorkerId,
        reason: str,
        retry: bool = True,
        now: datetime | None = None,
    ) -> WorkItem:
        item = super().fail(
            lease_id,
            worker_id=worker_id,
            reason=reason,
            retry=retry,
            now=now,
        )
        self._save()
        return item

    def cancel(
        self,
        work_item_id: WorkItemId,
        *,
        reason: str = "cancelled",
        now: datetime | None = None,
    ) -> WorkItem:
        item = super().cancel(work_item_id, reason=reason, now=now)
        self._save()
        return item

    def expire(self, *, now: datetime | None = None) -> tuple[WorkItem, ...]:
        expired = super().expire(now=now)
        if expired:
            self._save()
        return expired

    def _load(self) -> None:
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("file work queue payload must be an object")
        items = payload.get("items", [])
        if not isinstance(items, list):
            raise ValueError("file work queue items must be a list")
        loaded: dict[WorkItemId, WorkItem] = {}
        for index, item_payload in enumerate(items):
            if not isinstance(item_payload, dict):
                raise ValueError(f"file work queue items[{index}] must be an object")
            item = _decode_work_item(item_payload)
            loaded[item.work_item_id] = item
        self._items = loaded
        self._sequence = max(
            (_sequence_from_work_item_id(item_id) for item_id in loaded), default=0
        )

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = {
            "version": 1,
            "items": [_encode_work_item(item) for item in self.list()],
        }
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(self._path)


def _lease_deadline(now: datetime, ttl_seconds: float) -> datetime:
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    return now + timedelta(seconds=ttl_seconds)


def _normalize_accepted_kinds(accepted_kinds: Collection[str] | None) -> frozenset[str] | None:
    if accepted_kinds is None:
        return None
    normalized = frozenset(kind.strip() for kind in accepted_kinds)
    if any(not kind for kind in normalized):
        raise ValueError("accepted_kinds must not include empty kinds")
    return normalized


def _sort_key(item: WorkItem) -> tuple[int, datetime, str]:
    return (-item.priority, item.available_at, str(item.work_item_id))


def _encode_work_item(item: WorkItem) -> dict[str, object]:
    return {
        "work_item_id": str(item.work_item_id),
        "kind": item.kind,
        "payload": dict(item.payload),
        "session_id": None if item.session_id is None else str(item.session_id),
        "task_id": None if item.task_id is None else str(item.task_id),
        "action_id": None if item.action_id is None else str(item.action_id),
        "priority": item.priority,
        "max_attempts": item.max_attempts,
        "attempts": item.attempts,
        "status": item.status.value,
        "created_at": item.created_at.isoformat(),
        "available_at": item.available_at.isoformat(),
        "lease": None if item.lease is None else _encode_worker_lease(item.lease),
        "idempotency_key": item.idempotency_key,
        "completed_at": None if item.completed_at is None else item.completed_at.isoformat(),
        "failed_at": None if item.failed_at is None else item.failed_at.isoformat(),
        "cancelled_at": None if item.cancelled_at is None else item.cancelled_at.isoformat(),
        "last_error": item.last_error,
    }


def _decode_work_item(payload: dict[str, object]) -> WorkItem:
    return WorkItem(
        work_item_id=WorkItemId(_required_str(payload, "work_item_id")),
        kind=_required_str(payload, "kind"),
        payload=immutable_json(_optional_mapping(payload.get("payload"), "payload")),
        session_id=_optional_new_type(payload.get("session_id"), SessionId, "session_id"),
        task_id=_optional_new_type(payload.get("task_id"), TaskId, "task_id"),
        action_id=_optional_new_type(payload.get("action_id"), ActionId, "action_id"),
        priority=_required_int(payload, "priority"),
        max_attempts=_required_int(payload, "max_attempts"),
        attempts=_required_int(payload, "attempts"),
        status=WorkItemStatus(_required_str(payload, "status")),
        created_at=_required_datetime(payload, "created_at"),
        available_at=_required_datetime(payload, "available_at"),
        lease=_decode_optional_worker_lease(payload.get("lease")),
        idempotency_key=_optional_str(payload.get("idempotency_key"), "idempotency_key"),
        completed_at=_optional_datetime(payload.get("completed_at"), "completed_at"),
        failed_at=_optional_datetime(payload.get("failed_at"), "failed_at"),
        cancelled_at=_optional_datetime(payload.get("cancelled_at"), "cancelled_at"),
        last_error=_optional_str(payload.get("last_error"), "last_error"),
    )


def _encode_worker_lease(lease: WorkerLease) -> dict[str, object]:
    return {
        "lease_id": str(lease.lease_id),
        "worker_id": str(lease.worker_id),
        "leased_at": lease.leased_at.isoformat(),
        "lease_expires_at": lease.lease_expires_at.isoformat(),
        "heartbeat_at": lease.heartbeat_at.isoformat(),
    }


def _decode_optional_worker_lease(value: object) -> WorkerLease | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("lease must be an object")
    return WorkerLease(
        lease_id=LeaseId(_required_str(value, "lease_id")),
        worker_id=WorkerId(_required_str(value, "worker_id")),
        leased_at=_required_datetime(value, "leased_at"),
        lease_expires_at=_required_datetime(value, "lease_expires_at"),
        heartbeat_at=_required_datetime(value, "heartbeat_at"),
    )


def _required_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_str(value: object, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _required_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _required_datetime(payload: dict[str, object], key: str) -> datetime:
    return _parse_datetime(_required_str(payload, key), key)


def _optional_datetime(value: object, key: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return _parse_datetime(value, key)


def _parse_datetime(value: str, key: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an ISO datetime") from exc


def _optional_mapping(value: object, key: str) -> JsonMapping:
    if value is None:
        return immutable_json()
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return immutable_json(value)


def _optional_new_type[T](
    value: object,
    factory: Callable[[str], T],
    key: str,
) -> T | None:
    text = _optional_str(value, key)
    if text is None:
        return None
    return factory(text)


def _sequence_from_work_item_id(work_item_id: WorkItemId) -> int:
    value = str(work_item_id)
    if not value.startswith("work-"):
        return 0
    suffix = value.removeprefix("work-")
    if not suffix.isdecimal():
        return 0
    return int(suffix)
