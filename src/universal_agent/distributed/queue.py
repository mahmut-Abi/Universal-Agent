from __future__ import annotations

import json
import sqlite3
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from filelock import FileLock

from universal_agent.core import (
    ActionId,
    JsonMapping,
    SessionId,
    TaskId,
    immutable_json,
    utc_now,
)
from universal_agent.distributed.queue_codec import (
    _decode_work_item,
    _decode_work_queue_payload,
    _encode_work_item,
)
from universal_agent.distributed.queue_models import (
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

__all__ = [
    "FileWorkQueue",
    "InMemoryWorkQueue",
    "LeaseId",
    "LeaseLostError",
    "NoWorkAvailable",
    "SQLiteWorkQueue",
    "WorkItem",
    "WorkItemId",
    "WorkItemNotFoundError",
    "WorkItemStatus",
    "WorkerId",
    "WorkerLease",
]


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
    local durability by reloading one JSON document before public operations and
    atomically replacing it after every mutating operation. It is intentionally a
    local P6 primitive, not a cross-process locking or HA queue implementation.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        self._file_lock = FileLock(str(self._lock_path))
        self._lock_depth = 0
        with self._locked():
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
        with self._locked():
            self._load()
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
        with self._locked():
            self._load()
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
        with self._locked():
            self._load()
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
        with self._locked():
            self._load()
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
        with self._locked():
            self._load()
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
        with self._locked():
            self._load()
            item = super().cancel(work_item_id, reason=reason, now=now)
            self._save()
            return item

    def expire(self, *, now: datetime | None = None) -> tuple[WorkItem, ...]:
        with self._locked():
            self._load()
            expired = super().expire(now=now)
            if expired:
                self._save()
            return expired

    def prune_terminal(self, *, before: datetime | None = None) -> tuple[WorkItem, ...]:
        with self._locked():
            self._load()
            pruned = super().prune_terminal(before=before)
            if pruned:
                self._save()
            return pruned

    def get(self, work_item_id: WorkItemId) -> WorkItem:
        with self._locked():
            self._load()
            return super().get(work_item_id)

    def list(self, *, status: WorkItemStatus | None = None) -> tuple[WorkItem, ...]:
        with self._locked():
            self._load()
            return super().list(status=status)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        if self._lock_depth > 0:
            yield
            return
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._file_lock:
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1

    def _load(self) -> None:
        if not self._path.exists():
            self._items = {}
            self._sequence = 0
            return
        with self._path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        queue_payload = _decode_work_queue_payload(payload)
        if queue_payload.version != 1:
            raise ValueError(f"unsupported file work queue version: {queue_payload.version}")
        loaded: dict[WorkItemId, WorkItem] = {}
        for item_payload in queue_payload.items:
            item = _decode_work_item(item_payload)
            if item.work_item_id in loaded:
                raise ValueError(f"duplicate file work queue item: {item.work_item_id}")
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
            "items": [_encode_work_item(item) for item in super().list()],
        }
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(self._path)


class SQLiteWorkQueue(InMemoryWorkQueue):
    """SQLite-backed local WorkQueue adapter.

    This preserves the WorkQueue interface used by the scheduler, worker and
    coordinator while giving local deployments a durable queue that can share
    the runtime SQLite file. Mutations run under ``BEGIN IMMEDIATE`` so lease
    acquisition, retries, cancellation and idempotent enqueue observe one
    serialized queue state.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        self._transaction_connection: sqlite3.Connection | None = None
        with self._connect() as connection:
            _ensure_sqlite_work_queue_schema(connection)
            self._load(connection)

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
        with self._transaction() as connection:
            self._load(connection)
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
            self._save(connection)
            return item

    def lease(
        self,
        *,
        worker_id: WorkerId,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
        accepted_kinds: Collection[str] | None = None,
    ) -> WorkItem:
        with self._transaction(commit_on=(NoWorkAvailable,)) as connection:
            self._load(connection)
            timestamp = now or utc_now()
            kind_filter = _normalize_accepted_kinds(accepted_kinds)
            expired = InMemoryWorkQueue.expire(self, now=timestamp)
            item = self._next_leaseable(timestamp, accepted_kinds=kind_filter)
            if item is None:
                if expired:
                    self._save(connection)
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
            self._save(connection)
            return leased

    def heartbeat(
        self,
        lease_id: LeaseId,
        *,
        worker_id: WorkerId,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> WorkItem:
        with self._transaction(commit_on=(LeaseLostError,)) as connection:
            self._load(connection)
            item = super().heartbeat(
                lease_id,
                worker_id=worker_id,
                ttl_seconds=ttl_seconds,
                now=now,
            )
            self._save(connection)
            return item

    def complete(
        self,
        lease_id: LeaseId,
        *,
        worker_id: WorkerId,
        now: datetime | None = None,
    ) -> WorkItem:
        with self._transaction(commit_on=(LeaseLostError,)) as connection:
            self._load(connection)
            item = super().complete(lease_id, worker_id=worker_id, now=now)
            self._save(connection)
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
        with self._transaction(commit_on=(LeaseLostError,)) as connection:
            self._load(connection)
            item = super().fail(
                lease_id,
                worker_id=worker_id,
                reason=reason,
                retry=retry,
                now=now,
            )
            self._save(connection)
            return item

    def cancel(
        self,
        work_item_id: WorkItemId,
        *,
        reason: str = "cancelled",
        now: datetime | None = None,
    ) -> WorkItem:
        with self._transaction() as connection:
            self._load(connection)
            item = super().cancel(work_item_id, reason=reason, now=now)
            self._save(connection)
            return item

    def expire(self, *, now: datetime | None = None) -> tuple[WorkItem, ...]:
        with self._transaction() as connection:
            self._load(connection)
            expired = super().expire(now=now)
            if expired:
                self._save(connection)
            return expired

    def prune_terminal(self, *, before: datetime | None = None) -> tuple[WorkItem, ...]:
        with self._transaction() as connection:
            self._load(connection)
            pruned = super().prune_terminal(before=before)
            if pruned:
                self._save(connection)
            return pruned

    def get(self, work_item_id: WorkItemId) -> WorkItem:
        connection = self._transaction_connection
        if connection is not None:
            self._load(connection)
            return super().get(work_item_id)
        with self._connect() as fresh_connection:
            self._load(fresh_connection)
            return super().get(work_item_id)

    def list(self, *, status: WorkItemStatus | None = None) -> tuple[WorkItem, ...]:
        connection = self._transaction_connection
        if connection is not None:
            self._load(connection)
            return super().list(status=status)
        with self._connect() as fresh_connection:
            self._load(fresh_connection)
            return super().list(status=status)

    @contextmanager
    def _transaction(
        self,
        *,
        commit_on: tuple[type[Exception], ...] = (),
    ) -> Iterator[sqlite3.Connection]:
        active = self._transaction_connection
        if active is not None:
            yield active
            return
        with self._connect() as connection:
            _ensure_sqlite_work_queue_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            self._transaction_connection = connection
            try:
                yield connection
            except Exception as exc:
                if isinstance(exc, commit_on):
                    connection.commit()
                else:
                    connection.rollback()
                raise
            else:
                connection.commit()
            finally:
                self._transaction_connection = None

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self._path, timeout=30.0, isolation_level=None)

    def _load(self, connection: sqlite3.Connection) -> None:
        _ensure_sqlite_work_queue_schema(connection)
        rows = connection.execute(
            """
            SELECT payload
            FROM work_queue_items
            ORDER BY priority DESC, available_at ASC, work_item_id ASC
            """
        ).fetchall()
        loaded: dict[WorkItemId, WorkItem] = {}
        for row in rows:
            payload: object = json.loads(row[0])
            if not isinstance(payload, dict):
                raise ValueError("sqlite work queue item payload must be an object")
            item = _decode_work_item(payload)
            if item.work_item_id in loaded:
                raise ValueError(f"duplicate sqlite work queue item: {item.work_item_id}")
            loaded[item.work_item_id] = item
        self._items = loaded
        self._sequence = max(
            (_sequence_from_work_item_id(item_id) for item_id in loaded), default=0
        )

    def _save(self, connection: sqlite3.Connection) -> None:
        connection.execute("DELETE FROM work_queue_items")
        connection.executemany(
            """
            INSERT INTO work_queue_items(
                work_item_id,
                kind,
                status,
                priority,
                attempts,
                max_attempts,
                available_at,
                lease_expires_at,
                idempotency_key,
                payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_sqlite_work_item_row(item) for item in super().list()),
        )


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


def _ensure_sqlite_work_queue_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS work_queue_items (
            work_item_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            priority INTEGER NOT NULL,
            attempts INTEGER NOT NULL,
            max_attempts INTEGER NOT NULL,
            available_at TEXT NOT NULL,
            lease_expires_at TEXT,
            idempotency_key TEXT,
            payload TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_work_queue_items_leaseable
        ON work_queue_items(status, priority DESC, available_at ASC, work_item_id ASC)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_work_queue_items_idempotency
        ON work_queue_items(idempotency_key)
        """
    )


def _sqlite_work_item_row(
    item: WorkItem,
) -> tuple[str, str, str, int, int, int, str, str | None, str | None, str]:
    lease = item.lease
    return (
        str(item.work_item_id),
        item.kind,
        item.status.value,
        item.priority,
        item.attempts,
        item.max_attempts,
        item.available_at.isoformat(),
        None if lease is None else lease.lease_expires_at.isoformat(),
        item.idempotency_key,
        json.dumps(_encode_work_item(item), sort_keys=True, separators=(",", ":")),
    )
