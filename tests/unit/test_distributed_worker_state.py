from __future__ import annotations

import fcntl
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from universal_agent.core import immutable_json
from universal_agent.distributed import (
    FileWorkerRegistry,
    InMemoryWorkerRegistry,
    WorkerId,
    WorkerNotFoundError,
    WorkerStatus,
)


def test_worker_registry_registers_and_re_registers_worker() -> None:
    registry = InMemoryWorkerRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    first = registry.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        metadata=immutable_json({"host": "local"}),
        ttl_seconds=10,
        now=now,
    )
    second = registry.register(
        WorkerId("worker-a"),
        capabilities=("tool_action",),
        metadata=immutable_json({"host": "local-2"}),
        ttl_seconds=20,
        now=now + timedelta(seconds=1),
    )

    assert first.worker_id == WorkerId("worker-a")
    assert first.status is WorkerStatus.ONLINE
    assert first.registered_at == now
    assert first.lease_expires_at == now + timedelta(seconds=10)
    assert first.metadata["host"] == "local"
    assert second.registered_at == now
    assert second.heartbeat_at == now + timedelta(seconds=1)
    assert second.lease_expires_at == now + timedelta(seconds=21)
    assert second.capabilities == ("tool_action",)
    assert second.metadata["host"] == "local-2"
    assert registry.active() == (second,)


def test_file_worker_registry_persists_and_reloads_worker_state(tmp_path: Path) -> None:
    path = tmp_path / "workers.json"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    registry = FileWorkerRegistry(path)

    registered = registry.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        metadata=immutable_json({"host": "local"}),
        ttl_seconds=10,
        now=now,
    )
    renewed = FileWorkerRegistry(path).heartbeat(
        registered.worker_id,
        ttl_seconds=20,
        now=now + timedelta(seconds=5),
    )

    reloaded = FileWorkerRegistry(path)

    assert reloaded.get(WorkerId("worker-a")) == renewed
    assert reloaded.active() == (renewed,)
    assert renewed.metadata["host"] == "local"
    assert renewed.heartbeat_at == now + timedelta(seconds=5)
    assert renewed.lease_expires_at == now + timedelta(seconds=25)


def test_file_worker_registry_reloads_before_stale_writer_mutates(tmp_path: Path) -> None:
    path = tmp_path / "workers.json"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    owner = FileWorkerRegistry(path)
    owner.register(WorkerId("worker-a"), capabilities=("agent_session",), now=now)
    stale_writer = FileWorkerRegistry(path)

    owner.register(WorkerId("worker-b"), capabilities=("tool_action",), now=now)
    stale_writer.register(WorkerId("worker-c"), capabilities=("goal_execution",), now=now)

    assert tuple(record.worker_id for record in FileWorkerRegistry(path).list()) == (
        WorkerId("worker-a"),
        WorkerId("worker-b"),
        WorkerId("worker-c"),
    )


def test_file_worker_registry_serializes_cross_process_operations(tmp_path: Path) -> None:
    path = tmp_path / "workers.json"
    lock_path = path.with_suffix(path.suffix + ".lock")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    FileWorkerRegistry(path).register(WorkerId("worker-a"), now=now)

    lock_handle = lock_path.open("a+", encoding="utf-8")
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
    try:
        persisted = json.loads(path.read_text(encoding="utf-8"))
        assert persisted["workers"][0]["worker_id"] == "worker-a"
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import errno, fcntl, pathlib, sys; "
                    "handle = pathlib.Path(sys.argv[1]).open('a+', encoding='utf-8'); "
                    "\ntry:\n"
                    "    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                    "except BlockingIOError as exc:\n"
                    "    blocked = exc.errno in (errno.EACCES, errno.EAGAIN)\n"
                    "    print('blocked' if blocked else exc.errno)\n"
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
    finally:
        if not lock_handle.closed:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()

    assert probe.stdout.strip() == "blocked"


def test_worker_registry_heartbeat_extends_worker_lease() -> None:
    registry = InMemoryWorkerRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    registry.register(WorkerId("worker-a"), ttl_seconds=10, now=now)

    renewed = registry.heartbeat(
        WorkerId("worker-a"),
        ttl_seconds=20,
        now=now + timedelta(seconds=5),
    )

    assert renewed.status is WorkerStatus.ONLINE
    assert renewed.heartbeat_at == now + timedelta(seconds=5)
    assert renewed.lease_expires_at == now + timedelta(seconds=25)
    assert registry.get(WorkerId("worker-a")) == renewed


def test_worker_registry_drains_and_marks_worker_offline() -> None:
    registry = InMemoryWorkerRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    registry.register(WorkerId("worker-a"), now=now)

    draining = registry.drain(
        WorkerId("worker-a"),
        reason="finish current lease only",
        now=now + timedelta(seconds=1),
    )
    offline = registry.mark_offline(
        WorkerId("worker-a"),
        reason="shutdown complete",
        now=now + timedelta(seconds=2),
    )

    assert draining.status is WorkerStatus.DRAINING
    assert draining.last_error == "finish current lease only"
    assert registry.active() == ()
    assert offline.status is WorkerStatus.OFFLINE
    assert offline.last_error == "shutdown complete"
    assert registry.list(status=WorkerStatus.OFFLINE) == (offline,)


def test_worker_registry_expires_online_and_draining_workers() -> None:
    registry = InMemoryWorkerRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    online = registry.register(WorkerId("worker-a"), ttl_seconds=5, now=now)
    draining = registry.register(WorkerId("worker-b"), ttl_seconds=5, now=now)
    registry.drain(WorkerId("worker-b"), now=now + timedelta(seconds=1))
    registry.register(WorkerId("worker-c"), ttl_seconds=5, now=now)
    offline = registry.mark_offline(WorkerId("worker-c"), now=now + timedelta(seconds=1))

    expired = registry.expire(now=now + timedelta(seconds=6))

    assert [record.worker_id for record in expired] == [
        online.worker_id,
        draining.worker_id,
    ]
    assert registry.get(WorkerId("worker-a")).status is WorkerStatus.LOST
    assert registry.get(WorkerId("worker-b")).status is WorkerStatus.LOST
    assert registry.get(WorkerId("worker-c")) == offline
    assert registry.active() == ()


def test_worker_registry_rejects_heartbeat_after_expiry() -> None:
    registry = InMemoryWorkerRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    registry.register(WorkerId("worker-a"), ttl_seconds=5, now=now)

    with pytest.raises(WorkerNotFoundError, match="expired"):
        registry.heartbeat(WorkerId("worker-a"), now=now + timedelta(seconds=6))

    assert registry.get(WorkerId("worker-a")).status is WorkerStatus.LOST


def test_worker_registry_validates_inputs() -> None:
    registry = InMemoryWorkerRegistry()

    with pytest.raises(ValueError, match="worker_id"):
        registry.register(WorkerId(""))
    with pytest.raises(ValueError, match="ttl_seconds"):
        registry.register(WorkerId("worker-a"), ttl_seconds=0)
    with pytest.raises(ValueError, match="drain reason"):
        registry.register(WorkerId("worker-a"))
        registry.drain(WorkerId("worker-a"), reason=" ")
