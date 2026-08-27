from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from filelock import FileLock

from universal_agent.core import immutable_json
from universal_agent.distributed import (
    DistributedLockConflictError,
    DistributedLockLeaseLostError,
    DistributedLockOwnerId,
    FileDistributedLockRegistry,
    InMemoryDistributedLockRegistry,
    SQLiteDistributedLockRegistry,
)


def test_distributed_lock_registry_acquires_reenters_and_releases() -> None:
    registry = InMemoryDistributedLockRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    first = registry.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=10,
        metadata=immutable_json({"reason": "run session"}),
        now=now,
    )
    second = registry.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=20,
        now=now + timedelta(seconds=1),
    )
    released = registry.release(
        first.lease_id,
        owner_id=DistributedLockOwnerId("worker-a"),
        now=now + timedelta(seconds=2),
    )

    assert first == second
    assert first.lease_expires_at == now + timedelta(seconds=10)
    assert first.metadata["reason"] == "run session"
    assert released == first
    assert registry.active() == ()


def test_file_distributed_lock_registry_persists_and_reloads_leases(tmp_path: Path) -> None:
    path = tmp_path / "distributed-locks.json"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    registry = FileDistributedLockRegistry(path)

    lease = registry.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=10,
        metadata=immutable_json({"reason": "resume session"}),
        now=now,
    )
    renewed = FileDistributedLockRegistry(path).heartbeat(
        lease.lease_id,
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=20,
        now=now + timedelta(seconds=5),
    )

    reloaded = FileDistributedLockRegistry(path)

    assert reloaded.active() == (renewed,)
    assert renewed.metadata["reason"] == "resume session"
    assert renewed.heartbeat_at == now + timedelta(seconds=5)
    assert renewed.lease_expires_at == now + timedelta(seconds=25)


def test_file_distributed_lock_registry_restores_sequence(tmp_path: Path) -> None:
    path = tmp_path / "distributed-locks.json"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    first = FileDistributedLockRegistry(path).acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        now=now,
    )

    FileDistributedLockRegistry(path).release(
        first.lease_id,
        owner_id=DistributedLockOwnerId("worker-a"),
        now=now + timedelta(seconds=1),
    )
    second = FileDistributedLockRegistry(path).acquire(
        lock_key="session/session-2",
        owner_id=DistributedLockOwnerId("worker-b"),
        now=now + timedelta(seconds=2),
    )

    assert first.lease_id != second.lease_id
    assert str(second.lease_id) == "lock-lease-2"


def test_file_distributed_lock_registry_reloads_before_stale_writer_mutates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "distributed-locks.json"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    owner = FileDistributedLockRegistry(path)
    owner.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        now=now,
    )
    stale_writer = FileDistributedLockRegistry(path)

    owner.acquire(
        lock_key="session/session-2",
        owner_id=DistributedLockOwnerId("worker-b"),
        now=now + timedelta(seconds=1),
    )
    stale_writer.acquire(
        lock_key="session/session-3",
        owner_id=DistributedLockOwnerId("worker-c"),
        now=now + timedelta(seconds=2),
    )

    assert tuple(lease.lock_key for lease in FileDistributedLockRegistry(path).active()) == (
        "session/session-1",
        "session/session-2",
        "session/session-3",
    )


def test_sqlite_distributed_lock_registry_persists_and_reloads_leases(tmp_path: Path) -> None:
    path = tmp_path / "distributed-locks.sqlite3"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    registry = SQLiteDistributedLockRegistry(path)

    lease = registry.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=10,
        metadata=immutable_json({"reason": "resume session"}),
        now=now,
    )
    renewed = SQLiteDistributedLockRegistry(path).heartbeat(
        lease.lease_id,
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=20,
        now=now + timedelta(seconds=5),
    )

    reloaded = SQLiteDistributedLockRegistry(path)

    assert path.exists()
    assert reloaded.active() == (renewed,)
    assert renewed.metadata["reason"] == "resume session"
    assert renewed.heartbeat_at == now + timedelta(seconds=5)
    assert renewed.lease_expires_at == now + timedelta(seconds=25)


def test_sqlite_distributed_lock_registry_restores_sequence(tmp_path: Path) -> None:
    path = tmp_path / "distributed-locks.sqlite3"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    first = SQLiteDistributedLockRegistry(path).acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        now=now,
    )

    SQLiteDistributedLockRegistry(path).release(
        first.lease_id,
        owner_id=DistributedLockOwnerId("worker-a"),
        now=now + timedelta(seconds=1),
    )
    second = SQLiteDistributedLockRegistry(path).acquire(
        lock_key="session/session-2",
        owner_id=DistributedLockOwnerId("worker-b"),
        now=now + timedelta(seconds=2),
    )

    assert first.lease_id != second.lease_id
    assert str(second.lease_id) == "lock-lease-2"


def test_sqlite_distributed_lock_registry_reloads_before_stale_writer_mutates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "distributed-locks.sqlite3"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    owner = SQLiteDistributedLockRegistry(path)
    owner.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        now=now,
    )
    stale_writer = SQLiteDistributedLockRegistry(path)

    owner.acquire(
        lock_key="session/session-2",
        owner_id=DistributedLockOwnerId("worker-b"),
        now=now + timedelta(seconds=1),
    )
    stale_writer.acquire(
        lock_key="session/session-3",
        owner_id=DistributedLockOwnerId("worker-c"),
        now=now + timedelta(seconds=2),
    )

    assert tuple(lease.lock_key for lease in SQLiteDistributedLockRegistry(path).active()) == (
        "session/session-1",
        "session/session-2",
        "session/session-3",
    )


def test_sqlite_distributed_lock_registry_persists_expiry_on_lost_release(
    tmp_path: Path,
) -> None:
    path = tmp_path / "distributed-locks.sqlite3"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    lease = SQLiteDistributedLockRegistry(path).acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=5,
        now=now,
    )

    with pytest.raises(DistributedLockLeaseLostError, match="expired"):
        SQLiteDistributedLockRegistry(path).release(
            lease.lease_id,
            owner_id=DistributedLockOwnerId("worker-a"),
            now=now + timedelta(seconds=6),
        )

    assert SQLiteDistributedLockRegistry(path).active() == ()


def test_file_distributed_lock_registry_serializes_cross_process_operations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "distributed-locks.json"
    lock_path = path.with_suffix(path.suffix + ".lock")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    FileDistributedLockRegistry(path).acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        now=now,
    )

    with FileLock(str(lock_path)):
        persisted = json.loads(path.read_text(encoding="utf-8"))
        assert persisted["locks"][0]["lock_key"] == "session/session-1"
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys\n"
                    "from filelock import FileLock, Timeout\n"
                    "\ntry:\n"
                    "    with FileLock(sys.argv[1], timeout=0):\n"
                    "        print('acquired')\n"
                    "except Timeout:\n"
                    "    print('blocked')\n"
                    "else:\n"
                    "    print('acquired')\n"
                ),
                str(lock_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )

    assert probe.stdout.strip() == "blocked"


def test_file_distributed_lock_registry_rejects_unsupported_file_version(
    tmp_path: Path,
) -> None:
    path = tmp_path / "distributed-locks.json"
    path.write_text(json.dumps({"version": 2, "locks": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported file distributed lock version: 2"):
        FileDistributedLockRegistry(path)


def test_file_distributed_lock_registry_rejects_non_list_locks(tmp_path: Path) -> None:
    path = tmp_path / "distributed-locks.json"
    path.write_text(json.dumps({"version": 1, "locks": "bad"}), encoding="utf-8")

    with pytest.raises(ValueError, match="file distributed locks must be a list"):
        FileDistributedLockRegistry(path)


def test_file_distributed_lock_registry_rejects_non_object_lock_payload(
    tmp_path: Path,
) -> None:
    path = tmp_path / "distributed-locks.json"
    path.write_text(json.dumps({"version": 1, "locks": ["bad"]}), encoding="utf-8")

    with pytest.raises(ValueError, match=r"file distributed locks\[0\] must be an object"):
        FileDistributedLockRegistry(path)


def test_file_distributed_lock_registry_rejects_invalid_lease_datetime(
    tmp_path: Path,
) -> None:
    path = tmp_path / "distributed-locks.json"
    FileDistributedLockRegistry(path).acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    locks = payload["locks"]
    assert isinstance(locks, list)
    lease = locks[0]
    assert isinstance(lease, dict)
    lease["acquired_at"] = "not-a-date"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="acquired_at must be an ISO datetime"):
        FileDistributedLockRegistry(path)


def test_file_distributed_lock_registry_rejects_non_object_lease_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / "distributed-locks.json"
    FileDistributedLockRegistry(path).acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    locks = payload["locks"]
    assert isinstance(locks, list)
    lease = locks[0]
    assert isinstance(lease, dict)
    lease["metadata"] = "bad"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="metadata must be an object"):
        FileDistributedLockRegistry(path)


def test_distributed_lock_registry_rejects_conflicting_owner() -> None:
    registry = InMemoryDistributedLockRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    registry.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        now=now,
    )

    with pytest.raises(DistributedLockConflictError, match="worker-a"):
        registry.acquire(
            lock_key="session/session-1",
            owner_id=DistributedLockOwnerId("worker-b"),
            now=now,
        )


def test_distributed_lock_registry_heartbeat_extends_lease() -> None:
    registry = InMemoryDistributedLockRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    lease = registry.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=10,
        now=now,
    )

    renewed = registry.heartbeat(
        lease.lease_id,
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=20,
        now=now + timedelta(seconds=5),
    )

    assert renewed.lease_id == lease.lease_id
    assert renewed.heartbeat_at == now + timedelta(seconds=5)
    assert renewed.lease_expires_at == now + timedelta(seconds=25)
    assert registry.active() == (renewed,)


def test_distributed_lock_registry_expires_and_allows_new_owner() -> None:
    registry = InMemoryDistributedLockRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    first = registry.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=5,
        now=now,
    )

    expired = registry.expire(now=now + timedelta(seconds=6))
    second = registry.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-b"),
        ttl_seconds=5,
        now=now + timedelta(seconds=7),
    )

    assert expired == (first,)
    assert second.owner_id == DistributedLockOwnerId("worker-b")
    assert second.lease_id != first.lease_id


def test_distributed_lock_registry_rejects_lost_or_expired_lease_operations() -> None:
    registry = InMemoryDistributedLockRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    lease = registry.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=5,
        now=now,
    )

    with pytest.raises(DistributedLockLeaseLostError, match="another owner"):
        registry.heartbeat(
            lease.lease_id,
            owner_id=DistributedLockOwnerId("worker-b"),
            now=now + timedelta(seconds=1),
        )
    with pytest.raises(DistributedLockLeaseLostError, match="expired"):
        registry.release(
            lease.lease_id,
            owner_id=DistributedLockOwnerId("worker-a"),
            now=now + timedelta(seconds=6),
        )

    assert registry.active() == ()


def test_distributed_lock_registry_validates_inputs() -> None:
    registry = InMemoryDistributedLockRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(ValueError, match="lock_key"):
        registry.acquire(
            lock_key=" ",
            owner_id=DistributedLockOwnerId("worker-a"),
            now=now,
        )
    with pytest.raises(ValueError, match="owner_id"):
        registry.acquire(
            lock_key="session/session-1",
            owner_id=DistributedLockOwnerId(""),
            now=now,
        )
    with pytest.raises(ValueError, match="ttl_seconds"):
        registry.acquire(
            lock_key="session/session-1",
            owner_id=DistributedLockOwnerId("worker-a"),
            ttl_seconds=0,
            now=now,
        )
