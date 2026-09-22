"""Postgres store backend wiring tests (UA-PROD-001 direction).

The PostgresRuntimeStore itself is covered by test_postgres_persistence.py;
these tests cover the configuration and assembly-seam contract: url_env
resolution, validation errors, and the lazy 'postgres' extra requirement.
Live-connection behavior requires a real Postgres and stays out of unit CI.
"""

from __future__ import annotations

import pathlib
import sys
import types

import pytest

from universal_agent.configuration import (
    DomainConfig,
    RuntimeConfig,
    StoreBackend,
    StoreConfig,
)
from universal_agent.core import immutable_json
from universal_agent.host.runtime import _build_memory_store, _build_stores

pytestmark = pytest.mark.unit


def _runtime_config(store: StoreConfig) -> RuntimeConfig:
    return RuntimeConfig(
        environment=immutable_json({"environment": "local"}),
        store=store,
        domain=DomainConfig("local", "0.1.0"),
    )


def test_postgres_store_config_parses_url_env() -> None:
    config = StoreConfig.from_mapping({"backend": "postgres", "url_env": "AGENTD_PG_URL"})
    assert config.backend is StoreBackend.POSTGRES
    assert config.url_env == "AGENTD_PG_URL"


def test_postgres_store_config_requires_url_env() -> None:
    with pytest.raises(ValueError, match="url_env"):
        StoreConfig.from_mapping({"backend": "postgres"})


def test_postgres_store_config_rejects_path() -> None:
    with pytest.raises(ValueError, match="does not accept path"):
        StoreConfig.from_mapping({"backend": "postgres", "path": "/data", "url_env": "X"})


def test_build_stores_postgres_missing_env_fails_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _runtime_config(StoreConfig(StoreBackend.POSTGRES, url_env="AGENTD_PG_URL_MISSING"))
    monkeypatch.delenv("AGENTD_PG_URL_MISSING", raising=False)

    with pytest.raises(ValueError, match="AGENTD_PG_URL_MISSING"):
        _build_stores(config)


def test_build_stores_postgres_env_resolution_precedes_extra_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DSN env lookup runs before any store/import work: with the env set
    and the persistence package unavailable, the failure is the missing-extra
    message — proving env resolution (not a connection) happens first."""

    config = _runtime_config(StoreConfig(StoreBackend.POSTGRES, url_env="AGENTD_PG_URL"))
    monkeypatch.setenv("AGENTD_PG_URL", "postgresql://u:p@127.0.0.1:1/db")
    monkeypatch.setitem(sys.modules, "universal_agent.persistence", None)

    with pytest.raises(ValueError, match=r"postgres.*extra"):
        _build_stores(config)


def test_build_stores_postgres_forwards_tenant_id_to_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_postgres_store` forwards the config tenant scope to the store instead
    of silently falling back to the canonical default (Phase 0 §2). The real
    store class is swapped for a recording stub to avoid a live connection."""

    captured: list[dict[str, object]] = []

    class _FakePostgresRuntimeStore:
        def __init__(self, url: str, *, tenant_id: str = "default") -> None:
            captured.append({"url_scheme": url.split("://", 1)[0], "tenant_id": tenant_id})

    fake_module = types.SimpleNamespace(PostgresRuntimeStore=_FakePostgresRuntimeStore)
    monkeypatch.setitem(sys.modules, "universal_agent.persistence", fake_module)

    config = _runtime_config(
        StoreConfig(StoreBackend.POSTGRES, url_env="AGENTD_PG_URL", tenant_id="acme")
    )
    monkeypatch.setenv("AGENTD_PG_URL", "postgresql://u:p@127.0.0.1:1/db")

    _, _ = _build_stores(config)

    assert len(captured) == 1
    assert captured[0]["tenant_id"] == "acme"
    assert captured[0]["url_scheme"] == "postgresql"


def test_build_stores_postgres_defaults_tenant_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    class _FakePostgresRuntimeStore:
        def __init__(self, url: str, *, tenant_id: str = "default") -> None:
            captured.append({"tenant_id": tenant_id})

    fake_module = types.SimpleNamespace(PostgresRuntimeStore=_FakePostgresRuntimeStore)
    monkeypatch.setitem(sys.modules, "universal_agent.persistence", fake_module)

    config = _runtime_config(StoreConfig(StoreBackend.POSTGRES, url_env="AGENTD_PG_URL"))
    monkeypatch.setenv("AGENTD_PG_URL", "postgresql://u:p@127.0.0.1:1/db")

    _build_stores(config)

    assert captured == [{"tenant_id": "default"}]


@pytest.mark.unit
def test_memory_store_postgres_missing_env_fails_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Q6: the postgres memory factory resolves url_env and fails with a
    clear error before any driver import or connection attempt."""
    monkeypatch.delenv("AGENTD_PG_URL", raising=False)
    config = _runtime_config(
        StoreConfig.from_mapping({"backend": "postgres", "url_env": "AGENTD_PG_URL"})
    )
    factory = _build_memory_store(config)

    with pytest.raises(ValueError, match="AGENTD_PG_URL"):
        factory()


@pytest.mark.unit
def test_memory_store_file_backend_unchanged(tmp_path: pathlib.Path) -> None:
    config = _runtime_config(StoreConfig.from_mapping({"backend": "file", "path": str(tmp_path)}))
    store = _build_memory_store(config)()
    assert type(store).__name__ == "FileMemoryStore"
