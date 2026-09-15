"""Postgres store backend wiring tests (UA-PROD-001 direction).

The PostgresRuntimeStore itself is covered by test_postgres_persistence.py;
these tests cover the configuration and assembly-seam contract: url_env
resolution, validation errors, and the lazy 'postgres' extra requirement.
Live-connection behavior requires a real Postgres and stays out of unit CI.
"""

from __future__ import annotations

import sys

import pytest

from universal_agent.configuration import (
    DomainConfig,
    RuntimeConfig,
    StoreBackend,
    StoreConfig,
)
from universal_agent.core import immutable_json
from universal_agent.host.runtime import _build_stores

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
    config = _runtime_config(
        StoreConfig(StoreBackend.POSTGRES, url_env="AGENTD_PG_URL_MISSING")
    )
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
