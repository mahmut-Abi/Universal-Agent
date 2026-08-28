from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import NewType

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
    write_json,
)
from universal_agent.core.config_validation import (
    PydanticJsonValue,
    json_mapping,
    parse_non_empty_string,
    parse_positive_float,
    pydantic_error_details,
)

DistributedLockLeaseId = NewType("DistributedLockLeaseId", str)
DistributedLockOwnerId = NewType("DistributedLockOwnerId", str)


class DistributedLockConflictError(RuntimeError):
    pass


class DistributedLockLeaseLostError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DistributedLockLease:
    lock_key: str
    owner_id: DistributedLockOwnerId
    lease_id: DistributedLockLeaseId
    acquired_at: datetime
    lease_expires_at: datetime
    heartbeat_at: datetime
    metadata: JsonMapping = field(default_factory=immutable_json)


class _DistributedLockRegistryPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: StrictInt
    sequence: StrictInt | None = None
    locks: list[dict[str, object]] = Field(default_factory=list)


class _DistributedLockLeasePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lock_key: StrictStr
    owner_id: StrictStr
    lease_id: StrictStr
    acquired_at: datetime
    lease_expires_at: datetime
    heartbeat_at: datetime
    metadata: dict[str, PydanticJsonValue] | None = None


class InMemoryDistributedLockRegistry:
    """P6 local leased lock primitive for scheduler/worker coordination."""

    def __init__(self) -> None:
        self._leases: dict[str, DistributedLockLease] = {}
        self._sequence = 0

    def acquire(
        self,
        *,
        lock_key: str,
        owner_id: DistributedLockOwnerId,
        ttl_seconds: float = 30.0,
        metadata: JsonMapping | None = None,
        now: datetime | None = None,
    ) -> DistributedLockLease:
        timestamp = now or utc_now()
        _require_lock_key(lock_key)
        _require_owner_id(owner_id)
        self.expire(now=timestamp)
        existing = self._leases.get(lock_key)
        if existing is not None:
            if existing.owner_id == owner_id:
                return existing
            raise DistributedLockConflictError(f"lock is owned by {existing.owner_id}: {lock_key}")
        lease = DistributedLockLease(
            lock_key=lock_key,
            owner_id=owner_id,
            lease_id=self._next_lease_id(),
            acquired_at=timestamp,
            lease_expires_at=_lease_deadline(timestamp, ttl_seconds),
            heartbeat_at=timestamp,
            metadata=immutable_json(metadata),
        )
        self._leases[lock_key] = lease
        return lease

    def heartbeat(
        self,
        lease_id: DistributedLockLeaseId,
        *,
        owner_id: DistributedLockOwnerId,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> DistributedLockLease:
        timestamp = now or utc_now()
        lease = self._active_lease(lease_id, owner_id, now=timestamp)
        renewed = replace(
            lease,
            heartbeat_at=timestamp,
            lease_expires_at=_lease_deadline(timestamp, ttl_seconds),
        )
        self._leases[lease.lock_key] = renewed
        return renewed

    def release(
        self,
        lease_id: DistributedLockLeaseId,
        *,
        owner_id: DistributedLockOwnerId,
        now: datetime | None = None,
    ) -> DistributedLockLease:
        timestamp = now or utc_now()
        lease = self._active_lease(lease_id, owner_id, now=timestamp)
        del self._leases[lease.lock_key]
        return lease

    def expire(self, *, now: datetime | None = None) -> tuple[DistributedLockLease, ...]:
        timestamp = now or utc_now()
        expired: list[DistributedLockLease] = []
        for lock_key, lease in tuple(self._leases.items()):
            if lease.lease_expires_at > timestamp:
                continue
            del self._leases[lock_key]
            expired.append(lease)
        return tuple(sorted(expired, key=lambda item: item.lock_key))

    def active(self) -> tuple[DistributedLockLease, ...]:
        return tuple(self._leases[key] for key in sorted(self._leases))

    def _active_lease(
        self,
        lease_id: DistributedLockLeaseId,
        owner_id: DistributedLockOwnerId,
        *,
        now: datetime,
    ) -> DistributedLockLease:
        _require_owner_id(owner_id)
        found: DistributedLockLease | None = None
        for lease in self._leases.values():
            if lease.lease_id != lease_id:
                continue
            if lease.owner_id != owner_id:
                raise DistributedLockLeaseLostError(
                    f"lock lease is owned by another owner: {lease_id}"
                )
            found = lease
            break
        if found is None:
            raise DistributedLockLeaseLostError(f"lock lease not found: {lease_id}")
        if found.lease_expires_at <= now:
            self.expire(now=now)
            raise DistributedLockLeaseLostError(f"lock lease expired: {lease_id}")
        return found

    def _next_lease_id(self) -> DistributedLockLeaseId:
        self._sequence += 1
        return DistributedLockLeaseId(f"lock-lease-{self._sequence}")


class FileDistributedLockRegistry(InMemoryDistributedLockRegistry):
    """File-backed local distributed lock adapter.

    The lock semantics stay in the in-memory registry; this adapter reloads a
    JSON document under an advisory OS file lock before public operations and
    atomically replaces it after mutating operations. It is a local P6
    coordination primitive, not a distributed consensus implementation.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        self._file_lock = FileLock(str(self._lock_path))
        self._lock_depth = 0
        with self._locked():
            self._load()

    def acquire(
        self,
        *,
        lock_key: str,
        owner_id: DistributedLockOwnerId,
        ttl_seconds: float = 30.0,
        metadata: JsonMapping | None = None,
        now: datetime | None = None,
    ) -> DistributedLockLease:
        with self._locked():
            self._load()
            lease = super().acquire(
                lock_key=lock_key,
                owner_id=owner_id,
                ttl_seconds=ttl_seconds,
                metadata=metadata,
                now=now,
            )
            self._save()
            return lease

    def heartbeat(
        self,
        lease_id: DistributedLockLeaseId,
        *,
        owner_id: DistributedLockOwnerId,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> DistributedLockLease:
        with self._locked():
            self._load()
            lease = super().heartbeat(
                lease_id,
                owner_id=owner_id,
                ttl_seconds=ttl_seconds,
                now=now,
            )
            self._save()
            return lease

    def release(
        self,
        lease_id: DistributedLockLeaseId,
        *,
        owner_id: DistributedLockOwnerId,
        now: datetime | None = None,
    ) -> DistributedLockLease:
        with self._locked():
            self._load()
            lease = super().release(lease_id, owner_id=owner_id, now=now)
            self._save()
            return lease

    def expire(self, *, now: datetime | None = None) -> tuple[DistributedLockLease, ...]:
        with self._locked():
            self._load()
            expired = super().expire(now=now)
            if expired:
                self._save()
            return expired

    def active(self) -> tuple[DistributedLockLease, ...]:
        with self._locked():
            self._load()
            return super().active()

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
            self._leases = {}
            self._sequence = 0
            return
        payload = read_json_file(self._path)
        registry_payload = _decode_distributed_lock_registry_payload(payload)
        if registry_payload.version != 1:
            raise ValueError(
                f"unsupported file distributed lock version: {registry_payload.version}"
            )
        loaded: dict[str, DistributedLockLease] = {}
        for lease_payload in registry_payload.locks:
            lease = _decode_distributed_lock_lease(lease_payload)
            if lease.lock_key in loaded:
                raise ValueError(f"duplicate file distributed lock: {lease.lock_key}")
            loaded[lease.lock_key] = lease
        self._leases = loaded
        loaded_sequence = max(
            (_sequence_from_lock_lease_id(lease.lease_id) for lease in loaded.values()),
            default=0,
        )
        self._sequence = max(loaded_sequence, registry_payload.sequence or 0)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = {
            "version": 1,
            "sequence": self._sequence,
            "locks": [_encode_distributed_lock_lease(lease) for lease in super().active()],
        }
        with tmp_path.open("w", encoding="utf-8") as handle:
            write_json(handle, payload, indent=True)
        tmp_path.replace(self._path)


class SQLiteDistributedLockRegistry(InMemoryDistributedLockRegistry):
    """SQLite-backed local distributed lock adapter.

    This preserves the local leased-lock interface while adding durable state
    for embedded RuntimeHost deployments. It is still a local coordination
    primitive, not a distributed consensus implementation.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        self._transaction_connection: sqlite3.Connection | None = None
        with self._connect() as connection:
            _ensure_sqlite_lock_registry_schema(connection)
            self._load(connection)

    def acquire(
        self,
        *,
        lock_key: str,
        owner_id: DistributedLockOwnerId,
        ttl_seconds: float = 30.0,
        metadata: JsonMapping | None = None,
        now: datetime | None = None,
    ) -> DistributedLockLease:
        with self._transaction(commit_on=(DistributedLockConflictError,)) as connection:
            self._load(connection)
            timestamp = now or utc_now()
            _require_lock_key(lock_key)
            _require_owner_id(owner_id)
            expired = InMemoryDistributedLockRegistry.expire(self, now=timestamp)
            existing = self._leases.get(lock_key)
            if existing is not None:
                if expired:
                    self._save(connection)
                if existing.owner_id == owner_id:
                    return existing
                raise DistributedLockConflictError(
                    f"lock is owned by {existing.owner_id}: {lock_key}"
                )
            lease = DistributedLockLease(
                lock_key=lock_key,
                owner_id=owner_id,
                lease_id=self._next_lease_id(),
                acquired_at=timestamp,
                lease_expires_at=_lease_deadline(timestamp, ttl_seconds),
                heartbeat_at=timestamp,
                metadata=immutable_json(metadata),
            )
            self._leases[lock_key] = lease
            self._save(connection)
            return lease

    def heartbeat(
        self,
        lease_id: DistributedLockLeaseId,
        *,
        owner_id: DistributedLockOwnerId,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> DistributedLockLease:
        with self._transaction(commit_on=(DistributedLockLeaseLostError,)) as connection:
            self._load(connection)
            lease = super().heartbeat(
                lease_id,
                owner_id=owner_id,
                ttl_seconds=ttl_seconds,
                now=now,
            )
            self._save(connection)
            return lease

    def release(
        self,
        lease_id: DistributedLockLeaseId,
        *,
        owner_id: DistributedLockOwnerId,
        now: datetime | None = None,
    ) -> DistributedLockLease:
        with self._transaction(commit_on=(DistributedLockLeaseLostError,)) as connection:
            self._load(connection)
            lease = super().release(lease_id, owner_id=owner_id, now=now)
            self._save(connection)
            return lease

    def expire(self, *, now: datetime | None = None) -> tuple[DistributedLockLease, ...]:
        with self._transaction() as connection:
            self._load(connection)
            expired = super().expire(now=now)
            if expired:
                self._save(connection)
            return expired

    def active(self) -> tuple[DistributedLockLease, ...]:
        connection = self._transaction_connection
        if connection is not None:
            self._load(connection)
            return super().active()
        with self._connect() as fresh_connection:
            self._load(fresh_connection)
            return super().active()

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
            _ensure_sqlite_lock_registry_schema(connection)
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
        _ensure_sqlite_lock_registry_schema(connection)
        rows = connection.execute(
            """
            SELECT payload
            FROM distributed_lock_leases
            ORDER BY lock_key ASC
            """
        ).fetchall()
        loaded: dict[str, DistributedLockLease] = {}
        for row in rows:
            payload = loads_json(row[0])
            if not isinstance(payload, dict):
                raise ValueError("sqlite distributed lock payload must be an object")
            lease = _decode_distributed_lock_lease(payload)
            if lease.lock_key in loaded:
                raise ValueError(f"duplicate sqlite distributed lock: {lease.lock_key}")
            loaded[lease.lock_key] = lease
        sequence_row = connection.execute(
            "SELECT value FROM distributed_lock_state WHERE key = 'sequence'"
        ).fetchone()
        sequence = _sqlite_lock_sequence(sequence_row[0] if sequence_row is not None else None)
        loaded_sequence = max(
            (_sequence_from_lock_lease_id(lease.lease_id) for lease in loaded.values()),
            default=0,
        )
        self._leases = loaded
        self._sequence = max(sequence, loaded_sequence)

    def _save(self, connection: sqlite3.Connection) -> None:
        connection.execute("DELETE FROM distributed_lock_leases")
        connection.executemany(
            """
            INSERT INTO distributed_lock_leases(
                lock_key,
                owner_id,
                lease_id,
                lease_expires_at,
                payload
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (_sqlite_lock_lease_row(lease) for lease in super().active()),
        )
        connection.execute(
            """
            INSERT INTO distributed_lock_state(key, value)
            VALUES ('sequence', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(self._sequence),),
        )


def _encode_distributed_lock_lease(lease: DistributedLockLease) -> dict[str, object]:
    return {
        "lock_key": lease.lock_key,
        "owner_id": str(lease.owner_id),
        "lease_id": str(lease.lease_id),
        "acquired_at": lease.acquired_at.isoformat(),
        "lease_expires_at": lease.lease_expires_at.isoformat(),
        "heartbeat_at": lease.heartbeat_at.isoformat(),
        "metadata": dict(lease.metadata),
    }


def _decode_distributed_lock_lease(payload: dict[str, object]) -> DistributedLockLease:
    lease = _parse_distributed_lock_payload(_DistributedLockLeasePayload, payload)
    return DistributedLockLease(
        lock_key=lease.lock_key,
        owner_id=DistributedLockOwnerId(lease.owner_id),
        lease_id=DistributedLockLeaseId(lease.lease_id),
        acquired_at=lease.acquired_at,
        lease_expires_at=lease.lease_expires_at,
        heartbeat_at=lease.heartbeat_at,
        metadata=immutable_json(json_mapping(lease.metadata or {})),
    )


def _lease_deadline(now: datetime, ttl_seconds: float) -> datetime:
    parse_positive_float(ttl_seconds, "ttl_seconds")
    return now + timedelta(seconds=ttl_seconds)


def _require_lock_key(lock_key: str) -> None:
    parse_non_empty_string(lock_key, "lock_key")


def _require_owner_id(owner_id: DistributedLockOwnerId) -> None:
    parse_non_empty_string(str(owner_id), "owner_id")


def _decode_distributed_lock_registry_payload(payload: object) -> _DistributedLockRegistryPayload:
    try:
        return _DistributedLockRegistryPayload.model_validate(payload)
    except PydanticValidationError as exc:
        raise ValueError(_distributed_lock_registry_payload_error_message(exc)) from exc


def _parse_distributed_lock_payload[T: BaseModel](
    payload_type: type[T],
    payload: object,
) -> T:
    try:
        return payload_type.model_validate(payload)
    except PydanticValidationError as exc:
        raise ValueError(_distributed_lock_lease_payload_error_message(exc)) from exc


def _distributed_lock_registry_payload_error_message(
    error: PydanticValidationError,
) -> str:
    details = pydantic_error_details(error)
    path = details.path
    error_type = details.error_type
    if not error_type:
        return details.message
    if not path and error_type in {"model_attributes_type", "model_type"}:
        return "file distributed lock payload must be an object"
    if path == "locks" and error_type == "list_type":
        return "file distributed locks must be a list"
    if path.startswith("locks[") and error_type in {
        "dict_type",
        "model_attributes_type",
        "model_type",
    }:
        return f"file distributed {path} must be an object"
    if path == "version" and error_type in {"int_type", "missing"}:
        return "file distributed lock version must be an integer"
    if path == "sequence" and error_type == "int_type":
        return "file distributed lock sequence must be an integer"
    return details.message or str(error)


def _distributed_lock_lease_payload_error_message(
    error: PydanticValidationError,
) -> str:
    details = pydantic_error_details(error)
    path = details.path
    error_type = details.error_type
    if not error_type:
        return details.message
    if not path and error_type in {"model_attributes_type", "model_type"}:
        return "distributed lock lease payload must be an object"
    if "datetime" in error_type:
        return f"{path} must be an ISO datetime"
    expected = _expected_distributed_lock_lease_error_type(error_type, path)
    if expected is not None:
        return f"{path} must be {expected}"
    if details.message:
        return details.message.removeprefix("Value error, ")
    return str(error)


def _expected_distributed_lock_lease_error_type(error_type: str, path: str) -> str | None:
    if error_type == "missing":
        return _missing_distributed_lock_lease_field_type(path)
    return {
        "dict_type": "an object",
        "invalid-json-value": "JSON-compatible",
        "string_type": "a string",
    }.get(error_type)


def _missing_distributed_lock_lease_field_type(path: str) -> str:
    if path in {
        "acquired_at",
        "lease_expires_at",
        "heartbeat_at",
    }:
        return "an ISO datetime"
    if path == "metadata":
        return "an object"
    return "a string"


def _sequence_from_lock_lease_id(lease_id: DistributedLockLeaseId) -> int:
    value = str(lease_id)
    if not value.startswith("lock-lease-"):
        return 0
    suffix = value.removeprefix("lock-lease-")
    if not suffix.isdecimal():
        return 0
    return int(suffix)


def _ensure_sqlite_lock_registry_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS distributed_lock_leases (
            lock_key TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            lease_id TEXT NOT NULL UNIQUE,
            lease_expires_at TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS distributed_lock_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_distributed_lock_leases_expiry
        ON distributed_lock_leases(lease_expires_at ASC, lock_key ASC)
        """
    )


def _sqlite_lock_lease_row(
    lease: DistributedLockLease,
) -> tuple[str, str, str, str, str]:
    return (
        lease.lock_key,
        str(lease.owner_id),
        str(lease.lease_id),
        lease.lease_expires_at.isoformat(),
        dumps_json(_encode_distributed_lock_lease(lease)),
    )


def _sqlite_lock_sequence(value: object) -> int:
    if value is None:
        return 0
    if not isinstance(value, str) or not value.isdecimal():
        raise ValueError("sqlite distributed lock sequence must be a decimal string")
    return int(value)
