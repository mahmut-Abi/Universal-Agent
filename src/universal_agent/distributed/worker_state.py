from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from pydantic import ValidationError as PydanticValidationError

from universal_agent.core import (
    JsonMapping,
    dumps_json,
    immutable_json,
    loads_json,
    read_json_file,
    utc_now,
    write_json_file,
)
from universal_agent.core.config_validation import (
    PydanticJsonValue,
    json_mapping,
    parse_non_empty_string,
    parse_positive_float,
    pydantic_error_details,
)
from universal_agent.distributed.queue import WorkerId


class WorkerStatus(StrEnum):
    ONLINE = "online"
    DRAINING = "draining"
    OFFLINE = "offline"
    LOST = "lost"


class WorkerNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class WorkerRecord:
    worker_id: WorkerId
    status: WorkerStatus
    registered_at: datetime
    heartbeat_at: datetime
    lease_expires_at: datetime
    capabilities: tuple[str, ...] = ()
    metadata: JsonMapping = field(default_factory=immutable_json)
    last_error: str | None = None


class _WorkerRegistryPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: StrictInt
    workers: list[dict[str, object]] = Field(default_factory=list)


class _WorkerRecordPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    worker_id: StrictStr
    status: StrictStr
    registered_at: datetime
    heartbeat_at: datetime
    lease_expires_at: datetime
    capabilities: list[StrictStr]
    metadata: dict[str, PydanticJsonValue] | None = None
    last_error: StrictStr | None = None


class InMemoryWorkerRegistry:
    """P6 local worker registry with heartbeat/expiry state."""

    def __init__(self) -> None:
        self._workers: dict[WorkerId, WorkerRecord] = {}

    def register(
        self,
        worker_id: WorkerId,
        *,
        capabilities: tuple[str, ...] = (),
        metadata: JsonMapping | None = None,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> WorkerRecord:
        timestamp = now or utc_now()
        _require_worker_id(worker_id)
        _validate_ttl(ttl_seconds)
        record = self._workers.get(worker_id)
        if record is not None:
            updated = replace(
                record,
                status=WorkerStatus.ONLINE,
                heartbeat_at=timestamp,
                lease_expires_at=_lease_deadline(timestamp, ttl_seconds),
                capabilities=tuple(capabilities),
                metadata=immutable_json(metadata),
                last_error=None,
            )
        else:
            updated = WorkerRecord(
                worker_id=worker_id,
                status=WorkerStatus.ONLINE,
                registered_at=timestamp,
                heartbeat_at=timestamp,
                lease_expires_at=_lease_deadline(timestamp, ttl_seconds),
                capabilities=tuple(capabilities),
                metadata=immutable_json(metadata),
            )
        self._workers[worker_id] = updated
        return updated

    def heartbeat(
        self,
        worker_id: WorkerId,
        *,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> WorkerRecord:
        timestamp = now or utc_now()
        _validate_ttl(ttl_seconds)
        record = self.get(worker_id)
        if record.lease_expires_at <= timestamp:
            self.expire(now=timestamp)
            raise WorkerNotFoundError(f"worker lease expired: {worker_id}")
        updated = replace(
            record,
            heartbeat_at=timestamp,
            lease_expires_at=_lease_deadline(timestamp, ttl_seconds),
            status=WorkerStatus.ONLINE if record.status is WorkerStatus.LOST else record.status,
        )
        self._workers[worker_id] = updated
        return updated

    def drain(
        self,
        worker_id: WorkerId,
        *,
        reason: str = "worker draining",
        now: datetime | None = None,
    ) -> WorkerRecord:
        parse_non_empty_string(reason, "drain reason")
        record = self.get(worker_id)
        updated = replace(
            record,
            status=WorkerStatus.DRAINING,
            heartbeat_at=now or utc_now(),
            last_error=reason,
        )
        self._workers[worker_id] = updated
        return updated

    def mark_offline(
        self,
        worker_id: WorkerId,
        *,
        reason: str = "worker offline",
        now: datetime | None = None,
    ) -> WorkerRecord:
        parse_non_empty_string(reason, "offline reason")
        record = self.get(worker_id)
        updated = replace(
            record,
            status=WorkerStatus.OFFLINE,
            heartbeat_at=now or utc_now(),
            last_error=reason,
        )
        self._workers[worker_id] = updated
        return updated

    def expire(self, *, now: datetime | None = None) -> tuple[WorkerRecord, ...]:
        timestamp = now or utc_now()
        expired: list[WorkerRecord] = []
        for worker_id, record in tuple(self._workers.items()):
            if record.status in {WorkerStatus.OFFLINE, WorkerStatus.LOST}:
                continue
            if record.lease_expires_at > timestamp:
                continue
            lost = replace(
                record,
                status=WorkerStatus.LOST,
                heartbeat_at=timestamp,
                last_error=f"worker heartbeat expired: {worker_id}",
            )
            self._workers[worker_id] = lost
            expired.append(lost)
        return tuple(sorted(expired, key=lambda item: str(item.worker_id)))

    def get(self, worker_id: WorkerId) -> WorkerRecord:
        _require_worker_id(worker_id)
        try:
            return self._workers[worker_id]
        except KeyError as exc:
            raise WorkerNotFoundError(f"worker not found: {worker_id}") from exc

    def active(self) -> tuple[WorkerRecord, ...]:
        return tuple(
            sorted(
                (
                    record
                    for record in self._workers.values()
                    if record.status in {WorkerStatus.ONLINE, WorkerStatus.DRAINING}
                ),
                key=lambda item: str(item.worker_id),
            )
        )

    def list(self, *, status: WorkerStatus | None = None) -> tuple[WorkerRecord, ...]:
        records = tuple(sorted(self._workers.values(), key=lambda item: str(item.worker_id)))
        if status is None:
            return records
        return tuple(record for record in records if record.status is status)


class FileWorkerRegistry(InMemoryWorkerRegistry):
    """File-backed local worker registry adapter.

    The worker lifecycle semantics stay in the in-memory registry; this adapter
    reloads one JSON document under an advisory OS file lock before public
    operations and atomically replaces it after mutating operations.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        self._file_lock = FileLock(str(self._lock_path))
        self._lock_depth = 0
        with self._locked():
            self._load()

    def register(
        self,
        worker_id: WorkerId,
        *,
        capabilities: tuple[str, ...] = (),
        metadata: JsonMapping | None = None,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> WorkerRecord:
        with self._locked():
            self._load()
            record = super().register(
                worker_id,
                capabilities=capabilities,
                metadata=metadata,
                ttl_seconds=ttl_seconds,
                now=now,
            )
            self._save()
            return record

    def heartbeat(
        self,
        worker_id: WorkerId,
        *,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> WorkerRecord:
        with self._locked():
            self._load()
            record = super().heartbeat(worker_id, ttl_seconds=ttl_seconds, now=now)
            self._save()
            return record

    def drain(
        self,
        worker_id: WorkerId,
        *,
        reason: str = "worker draining",
        now: datetime | None = None,
    ) -> WorkerRecord:
        with self._locked():
            self._load()
            record = super().drain(worker_id, reason=reason, now=now)
            self._save()
            return record

    def mark_offline(
        self,
        worker_id: WorkerId,
        *,
        reason: str = "worker offline",
        now: datetime | None = None,
    ) -> WorkerRecord:
        with self._locked():
            self._load()
            record = super().mark_offline(worker_id, reason=reason, now=now)
            self._save()
            return record

    def expire(self, *, now: datetime | None = None) -> tuple[WorkerRecord, ...]:
        with self._locked():
            self._load()
            expired = super().expire(now=now)
            if expired:
                self._save()
            return expired

    def get(self, worker_id: WorkerId) -> WorkerRecord:
        with self._locked():
            self._load()
            return super().get(worker_id)

    def active(self) -> tuple[WorkerRecord, ...]:
        with self._locked():
            self._load()
            return super().active()

    def list(self, *, status: WorkerStatus | None = None) -> tuple[WorkerRecord, ...]:
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
            self._workers = {}
            return
        payload = read_json_file(self._path)
        registry_payload = _decode_worker_registry_payload(payload)
        if registry_payload.version != 1:
            raise ValueError(
                f"unsupported file worker registry version: {registry_payload.version}"
            )
        loaded: dict[WorkerId, WorkerRecord] = {}
        for worker_payload in registry_payload.workers:
            record = _decode_worker_record(worker_payload)
            if record.worker_id in loaded:
                raise ValueError(f"duplicate file worker registry worker: {record.worker_id}")
            loaded[record.worker_id] = record
        self._workers = loaded

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "workers": [_encode_worker_record(record) for record in super().list()],
        }
        write_json_file(self._path, payload, indent=True)


class SQLiteWorkerRegistry(InMemoryWorkerRegistry):
    """SQLite-backed local worker registry adapter."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        self._transaction_connection: sqlite3.Connection | None = None
        with self._connect() as connection:
            _ensure_sqlite_worker_registry_schema(connection)
            self._load(connection)

    def register(
        self,
        worker_id: WorkerId,
        *,
        capabilities: tuple[str, ...] = (),
        metadata: JsonMapping | None = None,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> WorkerRecord:
        with self._transaction() as connection:
            self._load(connection)
            record = super().register(
                worker_id,
                capabilities=capabilities,
                metadata=metadata,
                ttl_seconds=ttl_seconds,
                now=now,
            )
            self._save(connection)
            return record

    def heartbeat(
        self,
        worker_id: WorkerId,
        *,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> WorkerRecord:
        with self._transaction(commit_on=(WorkerNotFoundError,)) as connection:
            self._load(connection)
            record = super().heartbeat(worker_id, ttl_seconds=ttl_seconds, now=now)
            self._save(connection)
            return record

    def drain(
        self,
        worker_id: WorkerId,
        *,
        reason: str = "worker draining",
        now: datetime | None = None,
    ) -> WorkerRecord:
        with self._transaction() as connection:
            self._load(connection)
            record = super().drain(worker_id, reason=reason, now=now)
            self._save(connection)
            return record

    def mark_offline(
        self,
        worker_id: WorkerId,
        *,
        reason: str = "worker offline",
        now: datetime | None = None,
    ) -> WorkerRecord:
        with self._transaction() as connection:
            self._load(connection)
            record = super().mark_offline(worker_id, reason=reason, now=now)
            self._save(connection)
            return record

    def expire(self, *, now: datetime | None = None) -> tuple[WorkerRecord, ...]:
        with self._transaction() as connection:
            self._load(connection)
            expired = super().expire(now=now)
            if expired:
                self._save(connection)
            return expired

    def get(self, worker_id: WorkerId) -> WorkerRecord:
        connection = self._transaction_connection
        if connection is not None:
            self._load(connection)
            return super().get(worker_id)
        with self._connect() as fresh_connection:
            self._load(fresh_connection)
            return super().get(worker_id)

    def active(self) -> tuple[WorkerRecord, ...]:
        connection = self._transaction_connection
        if connection is not None:
            self._load(connection)
            return super().active()
        with self._connect() as fresh_connection:
            self._load(fresh_connection)
            return super().active()

    def list(self, *, status: WorkerStatus | None = None) -> tuple[WorkerRecord, ...]:
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
            _ensure_sqlite_worker_registry_schema(connection)
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
        _ensure_sqlite_worker_registry_schema(connection)
        rows = connection.execute(
            """
            SELECT payload
            FROM worker_registry_records
            ORDER BY worker_id ASC
            """
        ).fetchall()
        loaded: dict[WorkerId, WorkerRecord] = {}
        for row in rows:
            payload = loads_json(row[0])
            if not isinstance(payload, dict):
                raise ValueError("sqlite worker registry payload must be an object")
            record = _decode_worker_record(payload)
            if record.worker_id in loaded:
                raise ValueError(f"duplicate sqlite worker registry worker: {record.worker_id}")
            loaded[record.worker_id] = record
        self._workers = loaded

    def _save(self, connection: sqlite3.Connection) -> None:
        connection.execute("DELETE FROM worker_registry_records")
        connection.executemany(
            """
            INSERT INTO worker_registry_records(
                worker_id,
                status,
                heartbeat_at,
                lease_expires_at,
                payload
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (_sqlite_worker_record_row(record) for record in super().list()),
        )


def _encode_worker_record(record: WorkerRecord) -> dict[str, object]:
    return {
        "worker_id": str(record.worker_id),
        "status": record.status.value,
        "registered_at": record.registered_at.isoformat(),
        "heartbeat_at": record.heartbeat_at.isoformat(),
        "lease_expires_at": record.lease_expires_at.isoformat(),
        "capabilities": list(record.capabilities),
        "metadata": dict(record.metadata),
        "last_error": record.last_error,
    }


def _decode_worker_record(payload: dict[str, object]) -> WorkerRecord:
    record = _parse_worker_payload(_WorkerRecordPayload, payload)
    return WorkerRecord(
        worker_id=WorkerId(record.worker_id),
        status=WorkerStatus(record.status),
        registered_at=record.registered_at,
        heartbeat_at=record.heartbeat_at,
        lease_expires_at=record.lease_expires_at,
        capabilities=tuple(record.capabilities),
        metadata=immutable_json(json_mapping(record.metadata or {})),
        last_error=record.last_error,
    )


def _lease_deadline(now: datetime, ttl_seconds: float) -> datetime:
    return now + timedelta(seconds=ttl_seconds)


def _validate_ttl(ttl_seconds: float) -> None:
    parse_positive_float(ttl_seconds, "ttl_seconds")


def _require_worker_id(worker_id: WorkerId) -> None:
    parse_non_empty_string(str(worker_id), "worker_id")


def _decode_worker_registry_payload(payload: object) -> _WorkerRegistryPayload:
    try:
        return _WorkerRegistryPayload.model_validate(payload)
    except PydanticValidationError as exc:
        raise ValueError(_worker_registry_payload_error_message(exc)) from exc


def _parse_worker_payload[T: BaseModel](payload_type: type[T], payload: object) -> T:
    try:
        return payload_type.model_validate(payload)
    except PydanticValidationError as exc:
        raise ValueError(_worker_record_payload_error_message(exc)) from exc


def _worker_registry_payload_error_message(error: PydanticValidationError) -> str:
    details = pydantic_error_details(error)
    path = details.path
    error_type = details.error_type
    if not error_type:
        return details.message
    if not path and error_type in {"model_attributes_type", "model_type"}:
        return "file worker registry payload must be an object"
    if path == "workers" and error_type == "list_type":
        return "file worker registry workers must be a list"
    if path.startswith("workers[") and error_type in {
        "dict_type",
        "model_attributes_type",
        "model_type",
    }:
        return f"file worker registry {path} must be an object"
    if path == "version" and error_type in {"int_type", "missing"}:
        return "file worker registry version must be an integer"
    return details.message or str(error)


def _worker_record_payload_error_message(error: PydanticValidationError) -> str:
    details = pydantic_error_details(error)
    path = details.path
    error_type = details.error_type
    if not error_type:
        return details.message
    if not path and error_type in {"model_attributes_type", "model_type"}:
        return "worker record payload must be an object"
    if "datetime" in error_type:
        return f"{path} must be an ISO datetime"
    expected = _expected_worker_record_error_type(error_type, path)
    if expected is not None:
        return f"{path} must be {expected}"
    if details.message:
        return details.message.removeprefix("Value error, ")
    return str(error)


def _expected_worker_record_error_type(error_type: str, path: str) -> str | None:
    if error_type == "missing":
        return _missing_worker_record_field_type(path)
    return {
        "dict_type": "an object",
        "invalid-json-value": "JSON-compatible",
        "list_type": "a list",
        "string_type": "a string",
    }.get(error_type)


def _missing_worker_record_field_type(path: str) -> str:
    if path in {
        "registered_at",
        "heartbeat_at",
        "lease_expires_at",
    }:
        return "an ISO datetime"
    if path == "capabilities":
        return "a list"
    if path == "metadata":
        return "an object"
    return "a string"


def _ensure_sqlite_worker_registry_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS worker_registry_records (
            worker_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            lease_expires_at TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_worker_registry_records_status
        ON worker_registry_records(status, lease_expires_at ASC, worker_id ASC)
        """
    )


def _sqlite_worker_record_row(record: WorkerRecord) -> tuple[str, str, str, str, str]:
    return (
        str(record.worker_id),
        record.status.value,
        record.heartbeat_at.isoformat(),
        record.lease_expires_at.isoformat(),
        dumps_json(_encode_worker_record(record)),
    )
