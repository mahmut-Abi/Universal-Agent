"""Unit tests for the sqldb domain (UA-LIVE-2026-09-21 R6-1).

Covers: backend (SELECT validation, read-only enforcement, row clamping),
domain (capabilities/tools/policies), evidence extractor, and the
sql_query_ok completion claim.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime

import pytest

from universal_agent.core import immutable_json
from universal_agent.domains.sqldb.backend import (
    SqliteSqlBackend,
    SqlValidationError,
    _validate_select,
)
from universal_agent.domains.sqldb.domain import (
    SqldbDomain,
    SqldbEvidenceExtractor,
    SqlReadOnlyPolicy,
)

# --- backend: SELECT validation ----------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT * FROM t WHERE x = 1",
        "  SELECT 1  ",
        "SELECT 1;",
    ],
)
def test_validate_select_accepts_select(sql: str) -> None:
    assert _validate_select(sql) == sql.strip().rstrip(";").strip()


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET x = 1",
        "DELETE FROM t",
        "DROP TABLE t",
        "CREATE TABLE t (x INT)",
        "ALTER TABLE t ADD c INT",
        "ATTACH DATABASE 'x' AS y",
        "PRAGMA table_info(t)",
        "VACUUM",
        "REPLACE INTO t VALUES (1)",
    ],
)
def test_validate_select_rejects_non_select(sql: str) -> None:
    with pytest.raises(SqlValidationError, match="only SELECT statements"):
        _validate_select(sql)


def test_validate_select_rejects_empty() -> None:
    with pytest.raises(SqlValidationError, match="empty"):
        _validate_select("")


def test_validate_select_rejects_multiple_statements() -> None:
    with pytest.raises(SqlValidationError, match="multiple statements"):
        _validate_select("SELECT 1; DROP TABLE t")


# --- backend: read-only enforcement (physical) --------------------------------


@pytest.fixture()
def db_path(tmp_path: object) -> str:
    path = os.path.join(str(tmp_path), "test.db")
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE items (id INTEGER, name TEXT)")
    connection.execute("INSERT INTO items VALUES (1, 'alpha'), (2, 'beta'), (3, 'gamma')")
    connection.commit()
    connection.close()
    return path


@pytest.mark.asyncio
async def test_sqlite_backend_query_rows(db_path: str) -> None:
    backend = SqliteSqlBackend(db_path)
    result = await backend.query_rows(immutable_json({"sql": "SELECT * FROM items"}))

    assert result["row_count"] == 3
    assert result["columns"] == ["id", "name"]
    assert result["rows"] == [[1, "alpha"], [2, "beta"], [3, "gamma"]]
    assert result["sql_query_ok"] is True


@pytest.mark.asyncio
async def test_sqlite_backend_row_clamping(db_path: str) -> None:
    backend = SqliteSqlBackend(db_path)
    result = await backend.query_rows(immutable_json({"sql": "SELECT * FROM items", "limit": 2}))

    assert result["row_count"] == 2
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_sqlite_backend_read_only_enforcement(db_path: str) -> None:
    """Even if validation is bypassed, the read-only connection physically
    prevents writes."""
    backend = SqliteSqlBackend(db_path)
    connection = backend._connect()

    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        connection.execute("INSERT INTO items VALUES (99, 'nope')")

    connection.close()


@pytest.mark.asyncio
async def test_sqlite_backend_rejects_bad_sql(db_path: str) -> None:
    backend = SqliteSqlBackend(db_path)

    with pytest.raises(SqlValidationError):
        await backend.query_rows(immutable_json({"sql": "INSERT INTO items VALUES (1, 'x')"}))


# --- backend: inspect_tables with columns --------------------------------------


@pytest.mark.asyncio
async def test_sqlite_backend_inspect_tables_with_columns(db_path: str) -> None:
    backend = SqliteSqlBackend(db_path)
    result = await backend.inspect_tables(immutable_json({}))

    assert result["table_count"] >= 1
    tables = {t["name"]: t["columns"] for t in result["tables"] if isinstance(t, dict)}
    assert "items" in tables
    assert set(tables["items"]) == {"id", "name"}


# --- domain: capabilities + tools ----------------------------------------------


def test_sqldb_domain_capabilities() -> None:
    domain = SqldbDomain(StaticSqlBackendStub())

    caps = {c.name: c for c in domain.capabilities()}
    assert set(caps) == {"query_rows", "inspect_tables"}
    assert all(c.category.value == "observation" for c in caps.values())
    assert all(c.risk.value == "low" for c in caps.values())


def test_sqldb_domain_policies() -> None:
    domain = SqldbDomain(StaticSqlBackendStub())

    policies = {p.name: p for p in domain.policies()}
    assert "sqldb-read-only" in policies
    assert policies["sqldb-read-only"].effect.value == "allow"


# --- domain: read-only policy ---------------------------------------------------


def test_sqldb_policy_denies_insert() -> None:
    policy = SqlReadOnlyPolicy()
    context = _policy_context("INSERT INTO t VALUES (1)")

    result = policy.evaluate(context)

    assert result is not None
    assert result.effect.value == "deny"


def test_sqldb_policy_allows_select() -> None:
    policy = SqlReadOnlyPolicy()
    context = _policy_context("SELECT * FROM t")

    result = policy.evaluate(context)

    assert result is None  # None means "no objection" → allow


# --- domain: evidence extractor ---------------------------------------------------


def test_sqldb_evidence_extractor_produces_claims() -> None:
    from universal_agent.core import (
        ActionId,
        Observation,
        ObservationId,
        ObservationStatus,
        SessionId,
        Task,
        TaskId,
    )
    from universal_agent.evidence import EvidenceContext

    task = Task(TaskId("task-1"), "query", ("sql_query_ok",))
    observation = Observation(
        id=ObservationId("obs-1"),
        action_id=ActionId("action-1"),
        task_id=TaskId("task-1"),
        source="sqldb-evidence",
        status=ObservationStatus.SUCCEEDED,
        data=immutable_json({"sql": "SELECT 1", "row_count": 1, "sql_query_ok": True}),
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    extractor = SqldbEvidenceExtractor()
    context = EvidenceContext(
        session_id=SessionId("session-1"),
        task=task,
        observation=observation,
    )
    evidence = extractor.extract(context)

    claims = {e.claim: e.value for e in evidence}
    assert claims.get("sql_query_ok") is True
    assert claims.get("row_count") == 1


# --- helpers ---------------------------------------------------------------------


class StaticSqlBackendStub:
    """Minimal backend stub for domain-level tests (no real DB needed)."""

    async def query_rows(self, arguments):
        return immutable_json({"sql": "", "row_count": 0, "sql_query_ok": True})

    async def inspect_tables(self, arguments):
        return immutable_json({"tables": [], "table_count": 0})


def _policy_context(sql: str):
    from universal_agent.core import (
        ActionId,
        CapabilityCategory,
        CapabilityDefinition,
        GoalId,
        PolicyContext,
        SessionId,
        SideEffect,
        TaskId,
        ToolDefinition,
    )

    return PolicyContext(
        SessionId("session-1"),
        GoalId("goal-1"),
        TaskId("task-1"),
        ActionId("action-1"),
        CapabilityDefinition(
            "query_rows",
            "Run SELECT",
            CapabilityCategory.OBSERVATION,
        ),
        ToolDefinition(
            "sql_query_rows",
            "Run SELECT",
            ("query_rows",),
            side_effect=SideEffect.NONE,
        ),
        None,
        immutable_json({"sql": sql}),
        environment=immutable_json({"environment": "staging"}),
    )
