from __future__ import annotations

import fcntl
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from universal_agent.core import JsonMapping, immutable_json, utc_now
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
        if not reason.strip():
            raise ValueError("drain reason must not be empty")
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
        if not reason.strip():
            raise ValueError("offline reason must not be empty")
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
        with _file_worker_registry_lock(self._lock_path):
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1

    def _load(self) -> None:
        if not self._path.exists():
            self._workers = {}
            return
        with self._path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("file worker registry payload must be an object")
        version = payload.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError("file worker registry version must be an integer")
        if version != 1:
            raise ValueError(f"unsupported file worker registry version: {version}")
        workers = payload.get("workers", [])
        if not isinstance(workers, list):
            raise ValueError("file worker registry workers must be a list")
        loaded: dict[WorkerId, WorkerRecord] = {}
        for index, worker_payload in enumerate(workers):
            if not isinstance(worker_payload, dict):
                raise ValueError(f"file worker registry workers[{index}] must be an object")
            record = _decode_worker_record(worker_payload)
            if record.worker_id in loaded:
                raise ValueError(f"duplicate file worker registry worker: {record.worker_id}")
            loaded[record.worker_id] = record
        self._workers = loaded

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = {
            "version": 1,
            "workers": [_encode_worker_record(record) for record in super().list()],
        }
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(self._path)


@contextmanager
def _file_worker_registry_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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
    return WorkerRecord(
        worker_id=WorkerId(_required_str(payload, "worker_id")),
        status=WorkerStatus(_required_str(payload, "status")),
        registered_at=_required_datetime(payload, "registered_at"),
        heartbeat_at=_required_datetime(payload, "heartbeat_at"),
        lease_expires_at=_required_datetime(payload, "lease_expires_at"),
        capabilities=_required_str_tuple(payload.get("capabilities"), "capabilities"),
        metadata=immutable_json(_optional_mapping(payload.get("metadata"), "metadata")),
        last_error=_optional_str(payload.get("last_error"), "last_error"),
    )


def _lease_deadline(now: datetime, ttl_seconds: float) -> datetime:
    return now + timedelta(seconds=ttl_seconds)


def _validate_ttl(ttl_seconds: float) -> None:
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")


def _require_worker_id(worker_id: WorkerId) -> None:
    if not str(worker_id).strip():
        raise ValueError("worker_id must not be empty")


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


def _required_datetime(payload: dict[str, object], key: str) -> datetime:
    value = _required_str(payload, key)
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an ISO datetime") from exc


def _required_str_tuple(value: object, key: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{key}[{index}] must be a string")
        items.append(item)
    return tuple(items)


def _optional_mapping(value: object, key: str) -> JsonMapping:
    if value is None:
        return immutable_json()
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return immutable_json(value)
