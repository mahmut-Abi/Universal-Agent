from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum

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


def _lease_deadline(now: datetime, ttl_seconds: float) -> datetime:
    return now + timedelta(seconds=ttl_seconds)


def _validate_ttl(ttl_seconds: float) -> None:
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")


def _require_worker_id(worker_id: WorkerId) -> None:
    if not str(worker_id).strip():
        raise ValueError("worker_id must not be empty")
