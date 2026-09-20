"""StoreConfig tenant scoping for the Postgres backend (Phase 0).

Covers docs/phase0-principal-implementation.md §1: ``StoreConfig.tenant_id``
parsing, validation, ``effective_tenant_id`` normalization, and the rule that
only the POSTGRES backend accepts a tenant scope (FILE/SQLITE/MEMORY remain
single implicit tenant).
"""

from __future__ import annotations

import pytest

from universal_agent.configuration import StoreBackend, StoreConfig
from universal_agent.core import DEFAULT_TENANT_ID, JsonValue


def test_postgres_store_accepts_explicit_tenant_id() -> None:
    config = StoreConfig.from_mapping(
        {"backend": "postgres", "url_env": "PG_DSN", "tenant_id": "acme"}
    )
    assert config.backend is StoreBackend.POSTGRES
    assert config.tenant_id == "acme"
    assert config.effective_tenant_id == "acme"


def test_postgres_store_defaults_tenant_to_canonical_default() -> None:
    config = StoreConfig.from_mapping({"backend": "postgres", "url_env": "PG_DSN"})
    assert config.tenant_id is None
    assert config.effective_tenant_id == DEFAULT_TENANT_ID


def test_postgres_store_rejects_blank_tenant_id() -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        StoreConfig.from_mapping(
            {"backend": "postgres", "url_env": "PG_DSN", "tenant_id": ""}
        )


def test_postgres_store_rejects_whitespace_tenant_id() -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        StoreConfig.from_mapping(
            {"backend": "postgres", "url_env": "PG_DSN", "tenant_id": "   "}
        )


@pytest.mark.parametrize("backend", ["file", "sqlite", "memory"])
def test_non_postgres_backends_reject_tenant_id(backend: str) -> None:
    payload: dict[str, JsonValue] = {"backend": backend, "tenant_id": "acme"}
    if backend in {"file", "sqlite"}:
        payload["path"] = "/tmp/universal-agent/runtime.json"
    with pytest.raises(ValueError, match="only supported by the postgres store"):
        StoreConfig.from_mapping(payload)


def test_non_postgres_backend_without_tenant_id_is_unchanged() -> None:
    config = StoreConfig.from_mapping({"backend": "file", "path": "/tmp/x"})
    assert config.tenant_id is None
    assert config.effective_tenant_id == DEFAULT_TENANT_ID


def test_default_tenant_constant_is_shared_with_persistence() -> None:
    # The canonical default must be the literal persisted stores actually
    # write today. See docs/phase0-principal-implementation.md §1.3.
    from universal_agent.persistence.postgres import POSTGRES_DEFAULT_TENANT_ID

    assert DEFAULT_TENANT_ID == POSTGRES_DEFAULT_TENANT_ID