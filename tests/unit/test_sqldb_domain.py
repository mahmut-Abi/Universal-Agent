"""Unit tests for the sqldb domain.

Covers: backend (SELECT validation, read-only enforcement, row clamping),
domain (capabilities/tools/policies), evidence extractor, and the
sql_query_ok completion claim.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path as _Path

import pytest

from universal_agent.core import (
    ActionId,
    CapabilityCategory,
    CapabilityDefinition,
    GoalId,
    Observation,
    ObservationId,
    ObservationStatus,
    PolicyContext,
    PolicyEffect,
    SessionId,
    SideEffect,
    Task,
    TaskId,
    ToolDefinition,
    immutable_json,
)
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
from universal_agent.evidence import EvidenceContext

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
def db_path(tmp_path: _Path) -> str:
    p = tmp_path / "test.db"
    connection = sqlite3.connect(str(p))
    connection.execute("CREATE TABLE items (id INTEGER, name TEXT)")
    connection.execute("INSERT INTO items VALUES (1, 'alpha'), (2, 'beta'), (3, 'gamma')")
    connection.commit()
    connection.close()
    return str(p)


@pytest.mark.asyncio
async def test_sqlite_backend_query_rows(db_path: str) -> None:
    backend = SqliteSqlBackend(db_path)
    result = await backend.query_rows(immutable_json({"sql": "SELECT * FROM items"}))

    row_count = result["row_count"]
    assert isinstance(row_count, int) and row_count == 3
    columns = result["columns"]
    assert isinstance(columns, list)
    assert columns == ["id", "name"]
    rows = result["rows"]
    assert isinstance(rows, list)
    sql_ok = result["sql_query_ok"]
    assert isinstance(sql_ok, bool) and sql_ok is True


@pytest.mark.asyncio
async def test_sqlite_backend_row_clamping(db_path: str) -> None:
    backend = SqliteSqlBackend(db_path)
    result = await backend.query_rows(immutable_json({"sql": "SELECT * FROM items", "limit": 2}))

    assert result["row_count"] == 2
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_sqlite_backend_read_only_enforcement(db_path: str) -> None:
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

    table_count = result["table_count"]
    assert isinstance(table_count, int) and table_count >= 1
    raw = result["tables"]
    assert isinstance(raw, list)
    tables: dict[str, list[str]] = {}
    for item in raw:
        if isinstance(item, dict):
            name = str(item.get("name", ""))
            cols = item.get("columns", [])
            if isinstance(cols, list):
                tables[name] = [str(col) for col in cols]
    assert "items" in tables
    assert set(tables["items"]) == {"id", "name"}


# --- domain: capabilities + policies -------------------------------------------


class _StubBackend:
    async def query_rows(self, arguments):  # type: ignore[no-untyped-def]
        return immutable_json({"sql": "", "row_count": 0, "sql_query_ok": True})

    async def inspect_tables(self, arguments):  # type: ignore[no-untyped-def]
        return immutable_json({"tables": [], "table_count": 0})


def test_sqldb_domain_capabilities() -> None:
    domain = SqldbDomain(_StubBackend())

    caps = {c.name: c for c in domain.capabilities()}
    assert set(caps) == {"query_rows", "inspect_tables"}
    assert all(c.category.value == "observation" for c in caps.values())
    assert all(c.risk.value == "low" for c in caps.values())


def test_sqldb_domain_policies() -> None:
    domain = SqldbDomain(_StubBackend())

    names = [p.name for p in domain.policies()]
    assert "sqldb-read-only" in names


# --- domain: read-only policy ---------------------------------------------------


def _policy_context(sql: str) -> PolicyContext:
    return PolicyContext(
        session_id=SessionId("session-1"),
        goal_id=GoalId("goal-1"),
        task_id=TaskId("task-1"),
        action_id=ActionId("action-1"),
        capability=CapabilityDefinition(
            "query_rows",
            "Run SELECT",
            CapabilityCategory.OBSERVATION,
        ),
        tool=ToolDefinition(
            "sql_query_rows",
            "Run SELECT",
            ("query_rows",),
            side_effect=SideEffect.NONE,
        ),
        target=None,
        arguments=immutable_json({"sql": sql}),
        environment=immutable_json({"environment": "staging"}),
    )


def test_sqldb_policy_denies_insert() -> None:
    policy = SqlReadOnlyPolicy()
    context = _policy_context("INSERT INTO t VALUES (1)")

    result = policy.evaluate(context)

    assert result is not None
    assert result.effect is PolicyEffect.DENY


def test_sqldb_policy_allows_select() -> None:
    policy = SqlReadOnlyPolicy()
    context = _policy_context("SELECT * FROM t")

    result = policy.evaluate(context)

    assert result is None


# --- domain: evidence extractor ---------------------------------------------------


def test_sqldb_evidence_extractor_produces_claims() -> None:
    task = Task(description="query", required_criteria=("sql_query_ok",))
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
