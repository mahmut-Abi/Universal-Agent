from __future__ import annotations

from collections.abc import Collection, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Column,
    Engine,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy import insert as sql_insert
from sqlalchemy import select as sql_select
from sqlalchemy.engine import Connection

from universal_agent.core import (
    ActionId,
    JsonMapping,
    SessionId,
    TaskId,
    dumps_json,
    loads_json,
    utc_now,
)
from universal_agent.core.config_validation import (
    parse_json_object,
)
from universal_agent.distributed.queue_codec import (
    _decode_work_item,
    _encode_work_item,
)
from universal_agent.distributed.queue_memory import (
    InMemoryWorkQueue,
    _lease_deadline,
    _normalize_accepted_kinds,
    _sequence_from_work_item_id,
)
from universal_agent.distributed.queue_models import (
    LeaseId,
    LeaseLostError,
    NoWorkAvailable,
    WorkerId,
    WorkerLease,
    WorkItem,
    WorkItemId,
    WorkItemStatus,
)
from universal_agent.persistence.sqlite_engine import create_configured_sqlite_engine

_SQLITE_METADATA = MetaData()
_SQLITE_WORK_QUEUE_ITEMS = Table(
    "work_queue_items",
    _SQLITE_METADATA,
    Column("work_item_id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("status", String, nullable=False),
    Column("priority", Integer, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("max_attempts", Integer, nullable=False),
    Column("available_at", String, nullable=False),
    Column("lease_expires_at", String, nullable=True),
    Column("idempotency_key", String, nullable=True),
    Column("payload", Text, nullable=False),
)
Index(
    "idx_work_queue_items_leaseable",
    _SQLITE_WORK_QUEUE_ITEMS.c.status,
    _SQLITE_WORK_QUEUE_ITEMS.c.priority.desc(),
    _SQLITE_WORK_QUEUE_ITEMS.c.available_at.asc(),
    _SQLITE_WORK_QUEUE_ITEMS.c.work_item_id.asc(),
)
Index("idx_work_queue_items_idempotency", _SQLITE_WORK_QUEUE_ITEMS.c.idempotency_key)


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
        self._engine: Engine | None = None
        self._transaction_connection: Connection | None = None
        with self._connect() as connection:
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
    ) -> Iterator[Connection]:
        active = self._transaction_connection
        if active is not None:
            yield active
            return
        with self._connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
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

    @contextmanager
    def _connect(self) -> Iterator[Connection]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._sqlite_engine().connect() as connection:
            yield connection

    def _sqlite_engine(self) -> Engine:
        if self._engine is None:
            self._engine = create_configured_sqlite_engine(self._path)
            _SQLITE_METADATA.create_all(self._engine)
        return self._engine

    def _load(self, connection: Connection) -> None:
        # pi-lens-ignore: python-sql-injection
        rows = connection.execute(
            sql_select(_SQLITE_WORK_QUEUE_ITEMS.c.payload).order_by(
                _SQLITE_WORK_QUEUE_ITEMS.c.priority.desc(),
                _SQLITE_WORK_QUEUE_ITEMS.c.available_at.asc(),
                _SQLITE_WORK_QUEUE_ITEMS.c.work_item_id.asc(),
            )
        ).all()
        loaded: dict[WorkItemId, WorkItem] = {}
        for row in rows:
            payload = loads_json(row[0])
            item = _decode_work_item(
                dict(parse_json_object(payload, "sqlite work queue item payload"))
            )
            if item.work_item_id in loaded:
                raise ValueError(f"duplicate sqlite work queue item: {item.work_item_id}")
            loaded[item.work_item_id] = item
        self._items = loaded
        self._sequence = max(
            (_sequence_from_work_item_id(item_id) for item_id in loaded), default=0
        )

    def _save(self, connection: Connection) -> None:
        # pi-lens-ignore: python-sql-injection
        connection.execute(_SQLITE_WORK_QUEUE_ITEMS.delete())
        rows = [_sqlite_work_item_values(item) for item in super().list()]
        if rows:
            # pi-lens-ignore: python-sql-injection
            connection.execute(sql_insert(_SQLITE_WORK_QUEUE_ITEMS), rows)


def _sqlite_work_item_values(item: WorkItem) -> dict[str, str | int | None]:
    lease = item.lease
    return {
        "work_item_id": str(item.work_item_id),
        "kind": item.kind,
        "status": item.status.value,
        "priority": item.priority,
        "attempts": item.attempts,
        "max_attempts": item.max_attempts,
        "available_at": item.available_at.isoformat(),
        "lease_expires_at": None if lease is None else lease.lease_expires_at.isoformat(),
        "idempotency_key": item.idempotency_key,
        "payload": dumps_json(_encode_work_item(item)),
    }
