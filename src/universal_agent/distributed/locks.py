from __future__ import annotations

import fcntl
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import NewType

from universal_agent.core import JsonMapping, immutable_json, utc_now

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
        with _file_lock_registry_lock(self._lock_path):
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
        with self._path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("file distributed lock payload must be an object")
        version = payload.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError("file distributed lock version must be an integer")
        if version != 1:
            raise ValueError(f"unsupported file distributed lock version: {version}")
        leases = payload.get("locks", [])
        if not isinstance(leases, list):
            raise ValueError("file distributed locks must be a list")
        loaded: dict[str, DistributedLockLease] = {}
        for index, lease_payload in enumerate(leases):
            if not isinstance(lease_payload, dict):
                raise ValueError(f"file distributed locks[{index}] must be an object")
            lease = _decode_distributed_lock_lease(lease_payload)
            if lease.lock_key in loaded:
                raise ValueError(f"duplicate file distributed lock: {lease.lock_key}")
            loaded[lease.lock_key] = lease
        sequence = payload.get("sequence")
        if sequence is not None and (isinstance(sequence, bool) or not isinstance(sequence, int)):
            raise ValueError("file distributed lock sequence must be an integer")
        self._leases = loaded
        loaded_sequence = max(
            (_sequence_from_lock_lease_id(lease.lease_id) for lease in loaded.values()),
            default=0,
        )
        self._sequence = max(loaded_sequence, sequence or 0)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = {
            "version": 1,
            "sequence": self._sequence,
            "locks": [_encode_distributed_lock_lease(lease) for lease in super().active()],
        }
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(self._path)


@contextmanager
def _file_lock_registry_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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
    return DistributedLockLease(
        lock_key=_required_str(payload, "lock_key"),
        owner_id=DistributedLockOwnerId(_required_str(payload, "owner_id")),
        lease_id=DistributedLockLeaseId(_required_str(payload, "lease_id")),
        acquired_at=_required_datetime(payload, "acquired_at"),
        lease_expires_at=_required_datetime(payload, "lease_expires_at"),
        heartbeat_at=_required_datetime(payload, "heartbeat_at"),
        metadata=immutable_json(_optional_mapping(payload.get("metadata"), "metadata")),
    )


def _lease_deadline(now: datetime, ttl_seconds: float) -> datetime:
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    return now + timedelta(seconds=ttl_seconds)


def _require_lock_key(lock_key: str) -> None:
    if not lock_key.strip():
        raise ValueError("lock_key must not be empty")


def _require_owner_id(owner_id: DistributedLockOwnerId) -> None:
    if not str(owner_id).strip():
        raise ValueError("owner_id must not be empty")


def _required_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _required_datetime(payload: dict[str, object], key: str) -> datetime:
    value = _required_str(payload, key)
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


def _sequence_from_lock_lease_id(lease_id: DistributedLockLeaseId) -> int:
    value = str(lease_id)
    if not value.startswith("lock-lease-"):
        return 0
    suffix = value.removeprefix("lock-lease-")
    if not suffix.isdecimal():
        return 0
    return int(suffix)
