from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent import (
    DomainConfig,
    RuntimeConfig,
    RuntimeLimitsConfig,
    StoreBackend,
    StoreConfig,
)


def test_runtime_config_from_mapping_parses_typed_values() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "environment": {"environment": "production"},
            "store": {"backend": "file", "path": "/tmp/universal-agent"},
            "distributed_queue": {
                "backend": "file",
                "path": "/tmp/universal-agent/work-queue.json",
            },
            "distributed_locks": {
                "backend": "file",
                "path": "/tmp/universal-agent/distributed-locks.json",
            },
            "distributed_workers": {
                "backend": "file",
                "path": "/tmp/universal-agent/workers.json",
            },
            "limits": {"max_iterations": 7, "max_recovery_steps": 3},
            "domain": {"name": "kubernetes", "version": "0.2.0"},
        }
    )

    assert config.environment["environment"] == "production"
    assert config.store == StoreConfig.file("/tmp/universal-agent")
    assert config.distributed_queue == StoreConfig.file("/tmp/universal-agent/work-queue.json")
    assert config.distributed_locks == StoreConfig.file(
        "/tmp/universal-agent/distributed-locks.json"
    )
    assert config.distributed_workers == StoreConfig.file("/tmp/universal-agent/workers.json")
    assert config.store.backend is StoreBackend.FILE
    assert config.limits == RuntimeLimitsConfig(max_iterations=7, max_recovery_steps=3)
    assert config.domain == DomainConfig("kubernetes", "0.2.0")
    assert config.configured_domains() == (DomainConfig("kubernetes", "0.2.0"),)


def test_runtime_config_from_mapping_parses_multi_domain_values() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "domains": [
                {"name": "kubernetes", "version": "0.2.0"},
                {"name": "observability", "version": "0.1.0"},
            ],
        }
    )

    assert config.domain == DomainConfig("kubernetes", "0.2.0")
    assert config.configured_domains() == (
        DomainConfig("kubernetes", "0.2.0"),
        DomainConfig("observability", "0.1.0"),
    )


def test_runtime_config_from_json_file_parses_typed_values(tmp_path: Path) -> None:
    path = tmp_path / "runtime-config.json"
    path.write_text(
        """
        {
          "environment": {"environment": "production"},
          "store": {"backend": "file", "path": "/tmp/universal-agent"},
          "limits": {"max_iterations": 7, "max_recovery_steps": 3},
          "domain": {"name": "kubernetes", "version": "0.2.0"}
        }
        """,
        encoding="utf-8",
    )

    config = RuntimeConfig.from_json_file(path)

    assert config.environment["environment"] == "production"
    assert config.store == StoreConfig.file("/tmp/universal-agent")
    assert config.limits == RuntimeLimitsConfig(max_iterations=7, max_recovery_steps=3)
    assert config.domain == DomainConfig("kubernetes", "0.2.0")


def test_runtime_config_parses_sqlite_store() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "store": {"backend": "sqlite", "path": "/tmp/universal-agent/runtime.sqlite3"},
        }
    )

    assert config.store == StoreConfig.sqlite("/tmp/universal-agent/runtime.sqlite3")
    assert config.store.backend is StoreBackend.SQLITE


def test_runtime_config_parses_sqlite_distributed_locks() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "distributed_locks": {
                "backend": "sqlite",
                "path": "/tmp/universal-agent/distributed-locks.sqlite3",
            },
        }
    )

    assert config.distributed_locks == StoreConfig.sqlite(
        "/tmp/universal-agent/distributed-locks.sqlite3"
    )
    assert config.distributed_locks.backend is StoreBackend.SQLITE


def test_runtime_config_parses_sqlite_distributed_queue() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "distributed_queue": {
                "backend": "sqlite",
                "path": "/tmp/universal-agent/work-queue.sqlite3",
            },
        }
    )

    assert config.distributed_queue == StoreConfig.sqlite("/tmp/universal-agent/work-queue.sqlite3")
    assert config.distributed_queue.backend is StoreBackend.SQLITE


def test_runtime_config_parses_sqlite_distributed_workers() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "distributed_workers": {
                "backend": "sqlite",
                "path": "/tmp/universal-agent/workers.sqlite3",
            },
        }
    )

    assert config.distributed_workers == StoreConfig.sqlite("/tmp/universal-agent/workers.sqlite3")
    assert config.distributed_workers.backend is StoreBackend.SQLITE


def test_runtime_config_rejects_invalid_store_and_limits() -> None:
    with pytest.raises(ValueError, match="file store requires path"):
        StoreConfig.from_mapping({"backend": "file"})

    with pytest.raises(ValueError, match="memory store does not accept path"):
        StoreConfig.from_mapping({"backend": "memory", "path": "/tmp/runtime"})

    with pytest.raises(ValueError, match="sqlite store requires path"):
        StoreConfig.from_mapping({"backend": "sqlite"})

    with pytest.raises(ValueError, match="max_iterations must be positive"):
        RuntimeLimitsConfig(max_iterations=0).validate()

    with pytest.raises(ValueError, match="environment must be an object"):
        RuntimeConfig.from_mapping({"environment": "production"})

    with pytest.raises(ValueError, match="duplicate configured domains"):
        RuntimeConfig.from_mapping(
            {
                "domains": [
                    {"name": "kubernetes", "version": "0.2.0"},
                    {"name": "kubernetes", "version": "0.2.0"},
                ]
            }
        )


def test_runtime_config_rejects_non_object_json_file(tmp_path: Path) -> None:
    path = tmp_path / "runtime-config.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(ValueError, match="runtime config file must be an object"):
        RuntimeConfig.from_json_file(path)
