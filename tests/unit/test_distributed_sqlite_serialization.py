from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from universal_agent.distributed import (
    DistributedLockOwnerId,
    SQLiteDistributedLockRegistry,
    SQLiteWorkerRegistry,
    SQLiteWorkQueue,
    WorkerId,
    WorkItemStatus,
)


@pytest.mark.contract
def test_sqlite_work_queue_serializes_cross_process_writers(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    queue = SQLiteWorkQueue(path)
    queued = queue.enqueue(kind="agent_session", available_at=now)

    with _held_sqlite_writer(path):
        assert _probe_sqlite_writer(path) == "locked"

    leased = SQLiteWorkQueue(path).lease(worker_id=WorkerId("worker-a"), now=now)

    assert leased.work_item_id == queued.work_item_id
    assert SQLiteWorkQueue(path).get(queued.work_item_id).status is WorkItemStatus.LEASED


@pytest.mark.contract
def test_sqlite_distributed_lock_serializes_cross_process_writers(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    lease = SQLiteDistributedLockRegistry(path).acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=30,
        now=now,
    )

    with _held_sqlite_writer(path):
        assert _probe_sqlite_writer(path) == "locked"

    renewed = SQLiteDistributedLockRegistry(path).heartbeat(
        lease.lease_id,
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=30,
        now=now + timedelta(seconds=5),
    )

    assert renewed.lease_id == lease.lease_id
    assert renewed.heartbeat_at == now + timedelta(seconds=5)


@pytest.mark.contract
def test_sqlite_worker_registry_serializes_cross_process_writers(tmp_path: Path) -> None:
    path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    SQLiteWorkerRegistry(path).register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=30,
        now=now,
    )

    with _held_sqlite_writer(path):
        assert _probe_sqlite_writer(path) == "locked"

    renewed = SQLiteWorkerRegistry(path).heartbeat(
        WorkerId("worker-a"),
        ttl_seconds=30,
        now=now + timedelta(seconds=5),
    )

    assert renewed.worker_id == WorkerId("worker-a")
    assert renewed.heartbeat_at == now + timedelta(seconds=5)


class _held_sqlite_writer:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> None:
        self._connection = sqlite3.connect(self._path, timeout=0.1, isolation_level=None)
        self._connection.execute("BEGIN IMMEDIATE")

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        if self._connection is None:
            return
        self._connection.rollback()
        self._connection.close()


def _probe_sqlite_writer(path: Path) -> str:
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sqlite3, sys\n"
                "connection = sqlite3.connect(sys.argv[1], timeout=0.1, isolation_level=None)\n"
                "try:\n"
                "    connection.execute('BEGIN IMMEDIATE')\n"
                "except sqlite3.OperationalError as exc:\n"
                "    print('locked' if 'locked' in str(exc).lower() else str(exc))\n"
                "else:\n"
                "    print('acquired')\n"
                "    connection.rollback()\n"
                "finally:\n"
                "    connection.close()\n"
            ),
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return probe.stdout.strip()
