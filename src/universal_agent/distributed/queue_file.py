from __future__ import annotations

from collections.abc import Collection, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from filelock import FileLock

from universal_agent.core import (
    ActionId,
    JsonMapping,
    SessionId,
    TaskId,
    read_json_file,
    write_json_file,
)
from universal_agent.distributed.queue_codec import (
    _decode_work_item,
    _decode_work_queue_payload,
    _encode_work_item,
)
from universal_agent.distributed.queue_memory import (
    InMemoryWorkQueue,
    _sequence_from_work_item_id,
)
from universal_agent.distributed.queue_models import (
    LeaseId,
    WorkerId,
    WorkItem,
    WorkItemId,
    WorkItemStatus,
)


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
        payload = read_json_file(self._path)
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
        payload = {
            "version": 1,
            "items": [_encode_work_item(item) for item in super().list()],
        }
        write_json_file(self._path, payload, indent=True)
