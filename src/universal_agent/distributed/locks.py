from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
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
