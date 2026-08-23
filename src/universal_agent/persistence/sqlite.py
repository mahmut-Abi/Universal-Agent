from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from universal_agent.core import AgentState, EventId, RuntimeEvent, SessionId
from universal_agent.persistence.codec import (
    decode_runtime_event,
    decode_session_snapshot,
    encode_runtime_event,
    encode_session_snapshot,
    json_mapping,
)
from universal_agent.runtime.events import filter_events
from universal_agent.state import (
    SessionSnapshot,
    SessionVersionConflictError,
    StateNotFoundError,
    session_from_state,
)
from universal_agent.state.session import with_state


class SQLiteSessionStore:
    """SQLite-backed SessionStore adapter for local durable runtime deployments."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    async def create_session(self, snapshot: SessionSnapshot) -> None:
        snapshot.version = 0
        payload = _encode_json(encode_session_snapshot(snapshot))
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO sessions(session_id, created_at, payload)
                    VALUES (?, ?, ?)
                    """,
                    (
                        str(snapshot.state.session_id),
                        snapshot.state.goal.created_at.isoformat(),
                        payload,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"session already exists: {snapshot.state.session_id}") from exc

    async def list_sessions(self) -> tuple[SessionSnapshot, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload
                FROM sessions
                ORDER BY created_at DESC, session_id DESC
                """
            ).fetchall()
        return tuple(decode_session_snapshot(json_mapping(json.loads(row[0]))) for row in rows)

    async def load_session(self, session_id: SessionId) -> SessionSnapshot:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM sessions WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
        if row is None:
            raise StateNotFoundError(f"session not found: {session_id}")
        return decode_session_snapshot(json_mapping(json.loads(row[0])))

    async def save_session(self, snapshot: SessionSnapshot) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM sessions WHERE session_id = ?",
                (str(snapshot.state.session_id),),
            ).fetchone()
            if row is None:
                raise StateNotFoundError(f"session not found: {snapshot.state.session_id}")
            stored = decode_session_snapshot(json_mapping(json.loads(row[0])))
            if snapshot.version != stored.version:
                raise SessionVersionConflictError(
                    f"session version conflict: {snapshot.state.session_id} expected "
                    f"{stored.version}, got {snapshot.version}"
                )
            snapshot.version = stored.version + 1
            payload = _encode_json(encode_session_snapshot(snapshot))
            connection.execute(
                """
                UPDATE sessions
                SET created_at = ?, payload = ?
                WHERE session_id = ?
                """,
                (
                    snapshot.state.goal.created_at.isoformat(),
                    payload,
                    str(snapshot.state.session_id),
                ),
            )

    async def create(self, state: AgentState) -> None:
        await self.create_session(session_from_state(state))

    async def load(self, session_id: SessionId) -> AgentState:
        return (await self.load_session(session_id)).state

    async def save(self, state: AgentState) -> None:
        snapshot = await self.load_session(state.session_id)
        await self.save_session(with_state(snapshot, state))

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path)
        _ensure_schema(connection)
        return connection


class SQLiteEventStore:
    """SQLite-backed EventSink/EventReader adapter with cursor-compatible ordering."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    async def emit(self, event: RuntimeEvent) -> None:
        payload = _encode_json(encode_runtime_event(event))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_events(
                    event_id,
                    session_id,
                    goal_id,
                    task_id,
                    action_id,
                    type,
                    occurred_at,
                    payload
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.id),
                    str(event.session_id),
                    str(event.goal_id),
                    str(event.task_id),
                    None if event.action_id is None else str(event.action_id),
                    event.type,
                    event.occurred_at.isoformat(),
                    payload,
                ),
            )

    async def list_events(
        self,
        session_id: SessionId | None = None,
        *,
        after_event_id: EventId | None = None,
        limit: int | None = None,
    ) -> tuple[RuntimeEvent, ...]:
        with self._connect() as connection:
            if session_id is None:
                rows = connection.execute(
                    "SELECT payload FROM runtime_events ORDER BY sequence ASC"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT payload
                    FROM runtime_events
                    WHERE session_id = ?
                    ORDER BY sequence ASC
                    """,
                    (str(session_id),),
                ).fetchall()
        events = tuple(decode_runtime_event(json_mapping(json.loads(row[0]))) for row in rows)
        return filter_events(
            events,
            session_id=session_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path)
        _ensure_schema(connection)
        return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL,
            goal_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            action_id TEXT,
            type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runtime_events_session_sequence
        ON runtime_events(session_id, sequence)
        """
    )


def _encode_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
