from __future__ import annotations

import pytest

from universal_agent.persistence import (
    POSTGRES_SCHEMA_VERSION,
    postgres_schema_ddl,
    postgres_schema_table_names,
)
from universal_agent.persistence.postgres import PostgresRuntimeStore


@pytest.mark.contract
def test_postgres_schema_declares_runtime_tables_and_migration_version() -> None:
    assert POSTGRES_SCHEMA_VERSION == 1
    assert postgres_schema_table_names() == (
        "ua_runtime_event_outbox",
        "ua_runtime_events",
        "ua_schema_migrations",
        "ua_sessions",
    )


@pytest.mark.contract
def test_postgres_schema_uses_jsonb_and_tenant_scoped_keys() -> None:
    ddl = "\n".join(postgres_schema_ddl())

    assert "payload JSONB NOT NULL" in ddl
    assert "PRIMARY KEY (tenant_id, session_id)" in ddl
    assert "UNIQUE (tenant_id, event_id)" in ddl
    assert "ua_schema_migrations" in ddl


@pytest.mark.contract
def test_postgres_outbox_schema_supports_publisher_leasing() -> None:
    outbox = next(item for item in postgres_schema_ddl() if "ua_runtime_event_outbox" in item)

    assert "status VARCHAR NOT NULL" in outbox
    assert "attempts INTEGER NOT NULL" in outbox
    assert "available_at TIMESTAMP WITH TIME ZONE NOT NULL" in outbox
    assert "locked_by VARCHAR" in outbox
    assert "locked_until TIMESTAMP WITH TIME ZONE" in outbox
    assert "published_at TIMESTAMP WITH TIME ZONE" in outbox


@pytest.mark.unit
def test_postgres_runtime_store_requires_url_or_engine() -> None:
    with pytest.raises(ValueError, match="postgres runtime store requires a URL or engine"):
        PostgresRuntimeStore()
