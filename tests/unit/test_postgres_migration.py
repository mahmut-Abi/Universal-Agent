"""Schema v2 migration coverage (Phase 0).

The v2 ALTER step is verified locally without a live Postgres by capturing the
DDL a connection would execute. Fully live migrations (fresh vs legacy v1 → v2
schemas) are gated integration tests that run only when a Postgres DSN is
available (see `docker compose --profile postgres`).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    create_engine,
    insert,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.sql.expression import TextClause

from universal_agent.core import (
    DEFAULT_TENANT_ID,
    AgentState,
    Goal,
    SessionId,
    Task,
)
from universal_agent.persistence.postgres import (
    POSTGRES_DEFAULT_TENANT_ID,
    PostgresRuntimeStore,
    _apply_migration_step,
)
from universal_agent.state import session_from_state

_NOW = datetime.now(UTC)


class _CaptureConnection:
    """Records the SQL text a migration step would execute (test-only stand-in
    for a real SQLAlchemy ``Connection``)."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: TextClause, *args: object, **kwargs: object) -> None:
        self.statements.append(statement.text)


@pytest.mark.unit
def test_v2_migration_emits_user_id_alter_for_existing_table() -> None:
    """The v2 step adds the ownership column to an existing ua_sessions table —
    something ``create_all`` can never do (it only makes missing tables)."""

    connection = _CaptureConnection()
    _apply_migration_step(connection, 2)  # type: ignore[arg-type]

    assert len(connection.statements) == 1
    ddl = connection.statements[0].lower()
    assert "alter table ua_sessions" in ddl
    assert "add column if not exists" in ddl
    assert "user_id" in ddl
    assert "default 'system'" in ddl


@pytest.mark.unit
def test_v1_migration_step_is_noop() -> None:
    connection = _CaptureConnection()
    _apply_migration_step(connection, 1)  # type: ignore[arg-type]
    assert connection.statements == []


@pytest.mark.unit
def test_postgres_default_tenant_is_core_canonical_constant() -> None:
    # The persistence layer aliases the core canonical default so there is a
    # single literal for implicit tenancy across config and store.
    assert POSTGRES_DEFAULT_TENANT_ID == DEFAULT_TENANT_ID


# ---------------------------------------------------------------------------
# Live integration (skipped unless a Postgres DSN is configured)
# ---------------------------------------------------------------------------

_PG_URL = os.environ.get("UA_TEST_PG_URL") or os.environ.get("AGENTD_PG_URL")


def _require_pg() -> str:
    if not _PG_URL:
        pytest.skip("no Postgres DSN set (UA_TEST_PG_URL or AGENTD_PG_URL)")
    return _PG_URL


def _make_engine(dsn: str) -> Engine:
    return create_engine(dsn, isolation_level="AUTOCOMMIT")


def _fresh_database(admin_dsn: str, prefix: str) -> str:
    """Create and return the DSN for an isolated scratch database."""

    url = make_url(admin_dsn)
    dbname = f"{prefix}_{uuid.uuid4().hex[:8]}"
    admin = _make_engine(admin_dsn)
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {dbname}"))
    admin.dispose()
    return str(url.set(database=dbname))


def _drop_database(admin_dsn: str, target_dsn: str) -> None:
    dbname = make_url(target_dsn).database
    admin = _make_engine(admin_dsn)
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {dbname}"))
    admin.dispose()


def _make_state(session_id: str) -> AgentState:
    return AgentState(
        session_id=SessionId(session_id),
        goal=Goal(description="goal", success_criteria=()),
        current_task=Task(description="task", required_criteria=()),
        tasks=[Task(description="task", required_criteria=())],
    )


@pytest.mark.integration
def test_migrate_fresh_schema_applies_v1_and_v2_once() -> None:
    dsn = _require_pg()
    target = _fresh_database(dsn, "ua_test_fresh")

    store = PostgresRuntimeStore(url=target)
    assert store.tenant_id == DEFAULT_TENANT_ID
    assert store.user_id == "system"
    first = store.migrate()
    assert first.current_version == 3
    assert first.applied_versions == (1, 2, 3)
    second = store.migrate()
    assert second.current_version == 3
    assert second.applied_versions == ()

    store._engine.dispose()
    _drop_database(dsn, target)


@pytest.mark.integration
def test_migrate_v2_from_legacy_v1_schema_backfills_user_id() -> None:
    dsn = _require_pg()
    target = _fresh_database(dsn, "ua_test_legacy")

    # Build the legacy v1 shape: ua_sessions WITHOUT user_id plus a legacy row,
    # and record only migration version 1.
    engine = _make_engine(target)
    md = MetaData()
    Table(
        "ua_sessions",
        md,
        Column("tenant_id", String, nullable=False),
        Column("session_id", String, nullable=False),
        Column("version", Integer, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        Column("payload", JSONB, nullable=False),
        PrimaryKeyConstraint("tenant_id", "session_id"),
    )
    md.create_all(engine)

    from universal_agent.persistence.codec import encode_session_snapshot

    with engine.begin() as conn:
        conn.execute(
            insert(md.tables["ua_sessions"]).values(
                tenant_id="default",
                session_id="legacy-session",
                version=0,
                created_at=_NOW,
                updated_at=_NOW,
                payload=encode_session_snapshot(session_from_state(_make_state("legacy-session"))),
            )
        )
    engine.dispose()

    # Migrate the legacy DB to v3.
    store = PostgresRuntimeStore(url=target)
    assert store.migrate().current_version == 3

    # Legacy session is readable by the default-tenant store.
    loaded = asyncio.run(store.load_session(SessionId("legacy-session")))
    assert str(loaded.state.session_id) == "legacy-session"

    # The ownership column now exists and the legacy row backfilled to system.
    with store._engine.connect() as conn:
        row = conn.execute(
            text("SELECT tenant_id, user_id FROM ua_sessions WHERE session_id='legacy-session'")
        ).one()
    assert row.user_id == "system"

    store._engine.dispose()
    _drop_database(dsn, target)
