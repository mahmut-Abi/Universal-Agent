"""Read-only SQL backends for the sqldb domain.

Only SELECT statements are ever executed; the SQLite backend opens the
database in read-only mode and clamps row counts, so the domain physically
cannot mutate its target (defense in depth on top of the domain policy).
"""

from __future__ import annotations

import sqlite3
from typing import Protocol

from universal_agent.core import JsonMapping, JsonValue, immutable_json

_MAX_ROWS = 200


class SqlBackend(Protocol):
    """Read-only SQL query surface."""

    async def query_rows(self, arguments: JsonMapping) -> JsonMapping: ...

    async def inspect_tables(self, arguments: JsonMapping) -> JsonMapping: ...


class SqlValidationError(ValueError):
    """The request is not a read-only query the backend will run."""


def _validate_select(sql: str) -> str:
    """Reject anything that is not a single SELECT statement (defense in
    depth: the backend also opens read-only connections)."""

    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        raise SqlValidationError("sql query is empty")
    lowered = stripped.lower()
    forbidden_prefixes = (
        "insert",
        "update",
        "delete",
        "drop",
        "create",
        "alter",
        "attach",
        "detach",
        "pragma",
        "vacuum",
        "reindex",
        "replace",
        "with",
    )
    if lowered.startswith(forbidden_prefixes):
        raise SqlValidationError(f"only SELECT statements are allowed, got: {lowered.split()[0]}")
    if ";" in stripped:
        raise SqlValidationError("multiple statements are not allowed")
    return stripped


def _clamp_limit(arguments: JsonMapping) -> int:
    raw = arguments.get("limit")
    if raw is None:
        return _MAX_ROWS
    try:
        limit = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise SqlValidationError("limit must be an integer") from exc
    if limit <= 0:
        raise SqlValidationError("limit must be positive")
    return min(limit, _MAX_ROWS)


class SqliteSqlBackend:
    """Read-only SQLite backend: opens the database file in `mode=ro`.

    A multi-statement or mutating statement is rejected before execution and
    the read-only connection enforces it physically.
    """

    def __init__(self, path: str, *, timeout_seconds: float = 10.0) -> None:
        self._path = str(path)
        self._timeout_seconds = timeout_seconds

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"file:{self._path}?mode=ro",
            uri=True,
            timeout=self._timeout_seconds,
        )
        connection.row_factory = sqlite3.Row
        return connection

    async def query_rows(self, arguments: JsonMapping) -> JsonMapping:
        sql = _validate_select(str(arguments.get("sql", "")))
        limit = _clamp_limit(arguments)
        connection = self._connect()
        try:
            cursor = connection.execute(sql)
            columns = [description[0] for description in cursor.description or []]
            rows = [tuple(row) for row in cursor.fetchmany(limit)]
        except sqlite3.Error as exc:
            raise SqlValidationError(f"sqlite query failed: {exc}") from exc
        finally:
            connection.close()
        return immutable_json(
            {
                "sql": sql,
                "columns": columns,
                "rows": [list(row) for row in rows],
                "row_count": len(rows),
                "truncated": len(rows) == limit,
                # Stable completion claim: evidence flows to the world model so
                # goals can use `--success sql_query_ok=true` (the sqldb domain
                # has no natural 'healthy' analogue).
                "sql_query_ok": True,
            }
        )

    async def inspect_tables(self, arguments: JsonMapping) -> JsonMapping:
        connection = self._connect()
        try:
            cursor = connection.execute(
                "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
            )
            tables = []
            for name, kind in cursor.fetchall():
                columns = [col[1] for col in connection.execute(f"PRAGMA table_info({name})")]
                tables.append({"name": name, "type": kind, "columns": columns})
        except sqlite3.Error as exc:
            raise SqlValidationError(f"sqlite introspection failed: {exc}") from exc
        finally:
            connection.close()
        return immutable_json(
            {
                "tables": tables,
                "table_count": len(tables),
            }
        )


class StaticSqlBackend:
    """Fixture-friendly SQL backend for tests."""

    def __init__(
        self,
        *,
        query_response: JsonMapping | None = None,
        tables_response: JsonMapping | None = None,
    ) -> None:
        self._query_response = query_response or {
            "columns": ["name"],
            "rows": [["example"]],
            "row_count": 1,
            "truncated": False,
        }
        self._tables_response = tables_response or {
            "tables": [{"name": "example", "type": "table"}],
            "table_count": 1,
        }
        self.query_calls: list[JsonMapping] = []

    async def query_rows(self, arguments: JsonMapping) -> JsonMapping:
        self.query_calls.append(immutable_json(arguments))
        body: dict[str, JsonValue] = {
            "sql": str(arguments.get("sql", "")),
            **dict(self._query_response),
        }
        return immutable_json(body)

    async def inspect_tables(self, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"sql": "", **dict(self._tables_response)})


__all__ = ["SqlBackend", "SqlValidationError", "SqliteSqlBackend", "StaticSqlBackend"]
