from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from sqlalchemy import (
    Column,
    Engine,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    tuple_,
)
from sqlalchemy import insert as sql_insert
from sqlalchemy import select as sql_select
from sqlalchemy import update as sql_update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError as SQLAlchemyIntegrityError

from universal_agent.core import (
    AgentState,
    EventId,
    JsonMapping,
    RuntimeEvent,
    SessionId,
    dumps_json,
    loads_json,
    utc_now,
)
from universal_agent.core.config_validation import parse_json_object, parse_positive_int
from universal_agent.persistence.codec import (
    decode_runtime_event,
    decode_session_snapshot,
    encode_runtime_event,
    encode_session_snapshot,
)
from universal_agent.persistence.sqlite_engine import create_configured_sqlite_engine
from universal_agent.runtime.events import EventCursorError, poll_event_reader
from universal_agent.state import (
    SessionSnapshot,
    SessionVersionConflictError,
    StateNotFoundError,
    session_from_state,
)
from universal_agent.state.event_store import SESSION_STATE_EVENT
from universal_agent.state.session import with_state

_METADATA = MetaData()
_SESSIONS = Table(
    "sessions",
    _METADATA,
    Column("session_id", String, primary_key=True),
    Column("created_at", String, nullable=False),
    Column("version", Integer, nullable=False, default=0),
    Column("payload", Text, nullable=False),
)
_RUNTIME_EVENTS = Table(
    "runtime_events",
    _METADATA,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("event_id", String, nullable=False, unique=True),
    Column("session_id", String, nullable=False),
    Column("goal_id", String, nullable=False),
    Column("task_id", String, nullable=False),
    Column("action_id", String, nullable=True),
    Column("type", String, nullable=False),
    Column("occurred_at", String, nullable=False),
    Column("payload", Text, nullable=False),
)
_RUNTIME_EVENT_OUTBOX = Table(
    "runtime_event_outbox",
    _METADATA,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("event_id", String, nullable=False, unique=True),
    Column("session_id", String, nullable=False),
    Column("payload", Text, nullable=False),
    Column("published_at", String, nullable=True),
)
Index(
    "idx_runtime_events_session_sequence",
    _RUNTIME_EVENTS.c.session_id,
    _RUNTIME_EVENTS.c.sequence,
)
Index(
    "idx_runtime_event_outbox_pending_sequence",
    _RUNTIME_EVENT_OUTBOX.c.published_at,
    _RUNTIME_EVENT_OUTBOX.c.sequence,
)


@dataclass(frozen=True, slots=True)
class SQLiteOutboxEvent:
    sequence: int
    event_id: EventId
    session_id: SessionId
    event: RuntimeEvent


class SQLiteSessionStore:
    """SQLite-backed SessionStore adapter for local durable runtime deployments.

    Blocking SQLite work runs on worker threads through ``asyncio.to_thread``
    so a busy database (WAL ``busy_timeout``) never freezes the event loop.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._engine: Engine | None = None
        self._engine_lock = threading.Lock()

    async def create_session(self, snapshot: SessionSnapshot) -> None:
        await asyncio.to_thread(self._create_session_sync, snapshot)

    def _create_session_sync(self, snapshot: SessionSnapshot) -> None:
        snapshot.version = 0
        payload = _encode_json(encode_session_snapshot(snapshot))
        with self._connect() as connection:
            try:
                connection.execute(
                    sql_insert(_SESSIONS).values(
                        session_id=str(snapshot.state.session_id),
                        created_at=snapshot.state.goal.created_at.isoformat(),
                        version=snapshot.version,
                        payload=payload,
                    ),
                )
            except SQLAlchemyIntegrityError as exc:
                raise ValueError(f"session already exists: {snapshot.state.session_id}") from exc

    async def list_sessions(
        self,
        *,
        after_session_id: SessionId | None = None,
        limit: int | None = None,
    ) -> tuple[SessionSnapshot, ...]:
        return await asyncio.to_thread(self._list_sessions_sync, after_session_id, limit)

    def _list_sessions_sync(
        self,
        after_session_id: SessionId | None,
        limit: int | None,
    ) -> tuple[SessionSnapshot, ...]:
        if limit is not None and limit < 1:
            raise ValueError("session list limit must be positive")
        statement = sql_select(_SESSIONS.c.payload, _SESSIONS.c.version).order_by(
            _SESSIONS.c.created_at.desc(), _SESSIONS.c.session_id.desc()
        )
        with self._connect() as connection:
            if after_session_id is not None:
                cursor_key = self._session_cursor_key(connection, after_session_id)
                statement = statement.where(
                    tuple_(_SESSIONS.c.created_at, _SESSIONS.c.session_id) < cursor_key
                )
            if limit is not None:
                statement = statement.limit(limit)
            rows = connection.execute(statement).all()
        return tuple(_decode_stored_session(row[0], row[1]) for row in rows)

    @staticmethod
    def _session_cursor_key(connection: Connection, session_id: SessionId) -> tuple[str, str]:
        """Resolve the (created_at, session_id) ordering key of the cursor.

        Sessions strictly after the cursor in newest-first order are exactly
        those whose ordering key is smaller than the cursor's key.
        """
        row = connection.execute(
            sql_select(_SESSIONS.c.created_at, _SESSIONS.c.session_id).where(
                _SESSIONS.c.session_id == str(session_id)
            )
        ).first()
        if row is None:
            raise ValueError(f"session cursor not found: {session_id}")
        return (str(row[0]), str(row[1]))

    async def load_session(self, session_id: SessionId) -> SessionSnapshot:
        return await asyncio.to_thread(self._load_session_sync, session_id)

    def _load_session_sync(self, session_id: SessionId) -> SessionSnapshot:
        with self._connect() as connection:
            return _load_stored_session(connection, session_id)

    async def save_session(self, snapshot: SessionSnapshot) -> None:
        await asyncio.to_thread(self._save_session_sync, snapshot)

    def _save_session_sync(self, snapshot: SessionSnapshot) -> None:
        original_version = snapshot.version
        snapshot.version = original_version + 1
        payload = _encode_json(encode_session_snapshot(snapshot))
        with self._connect() as connection:
            result = connection.execute(
                sql_update(_SESSIONS)
                .where(_SESSIONS.c.session_id == str(snapshot.state.session_id))
                .where(_SESSIONS.c.version == original_version)
                .values(
                    created_at=snapshot.state.goal.created_at.isoformat(),
                    version=snapshot.version,
                    payload=payload,
                ),
            )
            if result.rowcount != 1:
                snapshot.version = original_version
                _raise_session_version_conflict(
                    connection,
                    snapshot.state.session_id,
                    original_version,
                )

    async def create(self, state: AgentState) -> None:
        await self.create_session(session_from_state(state))

    async def load(self, session_id: SessionId) -> AgentState:
        return (await self.load_session(session_id)).state

    async def save(self, state: AgentState) -> None:
        snapshot = await self.load_session(state.session_id)
        await self.save_session(with_state(snapshot, state))

    @contextmanager
    def _connect(self) -> Iterator[Connection]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._sqlite_engine().begin() as connection:
            yield connection

    def _sqlite_engine(self) -> Engine:
        if self._engine is None:
            with self._engine_lock:
                if self._engine is None:
                    self._engine = create_configured_sqlite_engine(self._path)
                    _METADATA.create_all(self._engine)
                    _ensure_sessions_version_column(self._engine)
        return self._engine


class SQLiteEventStore:
    """SQLite-backed EventSink/EventReader adapter with cursor-compatible ordering.

    Blocking SQLite work runs on worker threads through ``asyncio.to_thread``;
    the sync ``append``/``all``/``events_for`` surface stays synchronous for
    the EventStore protocol and replay paths.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._engine: Engine | None = None
        self._engine_lock = threading.Lock()

    async def emit(self, event: RuntimeEvent) -> None:
        await asyncio.to_thread(self.append, event)

    def append(self, event: RuntimeEvent) -> None:
        with self._connect() as connection:
            try:
                _insert_runtime_event(connection, event)
            except sqlite3.IntegrityError:
                # Event journals are idempotent by event id.
                return
            _insert_runtime_event_outbox(connection, event)

    def events_for(self, session_id: SessionId) -> tuple[RuntimeEvent, ...]:
        return tuple(event for event in self.all() if event.session_id == session_id)

    def all(self) -> tuple[RuntimeEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                sql_select(_RUNTIME_EVENTS.c.payload).order_by(_RUNTIME_EVENTS.c.sequence.asc())
            ).all()
        return tuple(decode_runtime_event(_loads_json_object(row[0])) for row in rows)

    async def list_events(
        self,
        session_id: SessionId | None = None,
        *,
        after_event_id: EventId | None = None,
        limit: int | None = None,
    ) -> tuple[RuntimeEvent, ...]:
        return await asyncio.to_thread(
            self._list_events_sync,
            session_id,
            after_event_id,
            limit,
        )

    def _list_events_sync(
        self,
        session_id: SessionId | None,
        after_event_id: EventId | None,
        limit: int | None,
    ) -> tuple[RuntimeEvent, ...]:
        if limit is not None:
            parse_positive_int(limit, "event stream limit")
        statement = (
            sql_select(_RUNTIME_EVENTS.c.payload)
            .where(_RUNTIME_EVENTS.c.type != SESSION_STATE_EVENT)
            .order_by(_RUNTIME_EVENTS.c.sequence.asc())
        )
        if session_id is not None:
            statement = statement.where(_RUNTIME_EVENTS.c.session_id == str(session_id))
        if after_event_id is not None:
            cursor_sequence = self._event_cursor_sequence(session_id, after_event_id)
            statement = statement.where(_RUNTIME_EVENTS.c.sequence > cursor_sequence)
        if limit is not None:
            statement = statement.limit(limit)
        with self._connect() as connection:
            rows = connection.execute(statement).all()
        return tuple(decode_runtime_event(_loads_json_object(row[0])) for row in rows)

    def _event_cursor_sequence(
        self,
        session_id: SessionId | None,
        after_event_id: EventId,
    ) -> int:
        """Resolve the journal sequence of an in-scope cursor event.

        The cursor must exist within the queried scope (session filter, and
        never a SessionStateCommitted event, which is excluded from results);
        otherwise it is out of scope, matching ``filter_events`` semantics.
        """
        statement = (
            sql_select(_RUNTIME_EVENTS.c.sequence)
            .where(_RUNTIME_EVENTS.c.event_id == str(after_event_id))
            .where(_RUNTIME_EVENTS.c.type != SESSION_STATE_EVENT)
        )
        if session_id is not None:
            statement = statement.where(_RUNTIME_EVENTS.c.session_id == str(session_id))
        with self._connect() as connection:
            row = connection.execute(statement).first()
        if row is None:
            raise EventCursorError(f"event cursor not found: {after_event_id}")
        sequence = row[0]
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise ValueError(f"invalid event sequence for cursor {after_event_id}")
        typed_sequence: int = sequence
        return typed_sequence

    async def watch_events(
        self,
        session_id: SessionId | None = None,
        *,
        after_event_id: EventId | None = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncGenerator[RuntimeEvent, None]:
        async for event in poll_event_reader(
            self,
            session_id,
            after_event_id=after_event_id,
            heartbeat_interval=heartbeat_interval,
        ):
            yield event

    def pending_outbox_events(self, *, limit: int | None = None) -> tuple[SQLiteOutboxEvent, ...]:
        if limit is not None and limit < 1:
            raise ValueError("outbox limit must be positive")
        statement = (
            sql_select(
                _RUNTIME_EVENT_OUTBOX.c.sequence,
                _RUNTIME_EVENT_OUTBOX.c.event_id,
                _RUNTIME_EVENT_OUTBOX.c.session_id,
                _RUNTIME_EVENT_OUTBOX.c.payload,
            )
            .where(_RUNTIME_EVENT_OUTBOX.c.published_at.is_(None))
            .order_by(_RUNTIME_EVENT_OUTBOX.c.sequence.asc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        with self._connect() as connection:
            rows = connection.execute(statement).all()
        return tuple(
            SQLiteOutboxEvent(
                _decode_outbox_sequence(row[0]),
                EventId(str(row[1])),
                SessionId(str(row[2])),
                decode_runtime_event(_loads_json_object(str(row[3]))),
            )
            for row in rows
        )

    def mark_outbox_published(self, event_ids: tuple[EventId, ...]) -> int:
        if not event_ids:
            return 0
        with self._connect() as connection:
            result = connection.execute(
                sql_update(_RUNTIME_EVENT_OUTBOX)
                .where(_RUNTIME_EVENT_OUTBOX.c.event_id.in_(tuple(str(item) for item in event_ids)))
                .where(_RUNTIME_EVENT_OUTBOX.c.published_at.is_(None))
                .values(published_at=utc_now().isoformat())
            )
        return result.rowcount or 0

    @contextmanager
    def _connect(self) -> Iterator[Connection]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._sqlite_engine().begin() as connection:
            yield connection

    def _sqlite_engine(self) -> Engine:
        if self._engine is None:
            with self._engine_lock:
                if self._engine is None:
                    self._engine = create_configured_sqlite_engine(self._path)
                    _METADATA.create_all(self._engine)
                    _ensure_sessions_version_column(self._engine)
        return self._engine


class SQLiteRuntimeStore(SQLiteSessionStore, SQLiteEventStore):
    """SQLite adapter that can commit a SessionSnapshot and RuntimeEvent atomically."""

    state_event_commit_strategy = "sqlite_transaction"

    async def commit_session_event(
        self,
        snapshot: SessionSnapshot,
        event: RuntimeEvent,
    ) -> None:
        await asyncio.to_thread(self._commit_session_event_sync, snapshot, event)

    def _commit_session_event_sync(
        self,
        snapshot: SessionSnapshot,
        event: RuntimeEvent,
    ) -> None:
        original_version = snapshot.version
        snapshot.version = original_version + 1
        payload = _encode_json(encode_session_snapshot(snapshot))
        with self._connect() as connection:
            try:
                result = connection.execute(
                    sql_update(_SESSIONS)
                    .where(_SESSIONS.c.session_id == str(snapshot.state.session_id))
                    .where(_SESSIONS.c.version == original_version)
                    .values(
                        created_at=snapshot.state.goal.created_at.isoformat(),
                        version=snapshot.version,
                        payload=payload,
                    ),
                )
                if result.rowcount != 1:
                    snapshot.version = original_version
                    _raise_session_version_conflict(
                        connection,
                        snapshot.state.session_id,
                        original_version,
                    )
                _insert_runtime_event(connection, event)
                _insert_runtime_event_outbox(connection, event)
            except SQLAlchemyIntegrityError as exc:
                snapshot.version = original_version
                _raise_sqlite_integrity_error(exc)
            except Exception:
                snapshot.version = original_version
                raise


def _decode_outbox_sequence(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("sqlite outbox sequence must be an integer")
    return value


def _encode_json(payload: object) -> str:
    return dumps_json(payload)


def _loads_json_object(value: str | bytes | bytearray) -> JsonMapping:
    return parse_json_object(loads_json(value), "sqlite payload")


def _decode_stored_session(
    payload: str | bytes | bytearray,
    version: object,
) -> SessionSnapshot:
    snapshot = decode_session_snapshot(_loads_json_object(payload))
    snapshot.version = _decode_version(version)
    return snapshot


def _load_stored_session(connection: Connection, session_id: SessionId) -> SessionSnapshot:
    row = connection.execute(
        sql_select(_SESSIONS.c.payload, _SESSIONS.c.version).where(
            _SESSIONS.c.session_id == str(session_id)
        )
    ).first()
    if row is None:
        raise StateNotFoundError(f"session not found: {session_id}")
    return _decode_stored_session(row[0], row[1])


def _raise_session_version_conflict(
    connection: Connection,
    session_id: SessionId,
    attempted_version: int,
) -> NoReturn:
    row = connection.execute(
        sql_select(_SESSIONS.c.version).where(_SESSIONS.c.session_id == str(session_id))
    ).first()
    if row is None:
        raise StateNotFoundError(f"session not found: {session_id}")
    stored_version = _decode_version(row[0])
    raise SessionVersionConflictError(
        f"session version conflict: {session_id} expected {stored_version}, got {attempted_version}"
    )


def _ensure_sessions_version_column(engine: Engine) -> None:
    with engine.begin() as connection:
        columns = {
            str(row[1]) for row in connection.exec_driver_sql("PRAGMA table_info(sessions)").all()
        }
        if "version" in columns:
            return
        connection.exec_driver_sql(
            "ALTER TABLE sessions ADD COLUMN version INTEGER NOT NULL DEFAULT 0"
        )
        rows = connection.execute(sql_select(_SESSIONS.c.session_id, _SESSIONS.c.payload)).all()
        for row in rows:
            snapshot = decode_session_snapshot(_loads_json_object(row[1]))
            connection.execute(
                sql_update(_SESSIONS)
                .where(_SESSIONS.c.session_id == str(row[0]))
                .values(version=snapshot.version)
            )


def _decode_version(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("sqlite session version must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str | bytes | bytearray):
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError("sqlite session version must be an integer") from exc
    raise ValueError("sqlite session version must be an integer")


def _insert_runtime_event(connection: Connection, event: RuntimeEvent) -> None:
    payload = _encode_json(encode_runtime_event(event))
    try:
        connection.execute(
            sql_insert(_RUNTIME_EVENTS).values(
                event_id=str(event.id),
                session_id=str(event.session_id),
                goal_id=str(event.goal_id),
                task_id=str(event.task_id),
                action_id=None if event.action_id is None else str(event.action_id),
                type=event.type,
                occurred_at=event.occurred_at.isoformat(),
                payload=payload,
            ),
        )
    except SQLAlchemyIntegrityError as exc:
        _raise_sqlite_integrity_error(exc)


def _insert_runtime_event_outbox(connection: Connection, event: RuntimeEvent) -> None:
    payload = _encode_json(encode_runtime_event(event))
    try:
        connection.execute(
            sql_insert(_RUNTIME_EVENT_OUTBOX).values(
                event_id=str(event.id),
                session_id=str(event.session_id),
                payload=payload,
                published_at=None,
            )
        )
    except SQLAlchemyIntegrityError as exc:
        _raise_sqlite_integrity_error(exc)


def _raise_sqlite_integrity_error(error: SQLAlchemyIntegrityError) -> NoReturn:
    if isinstance(error.orig, sqlite3.IntegrityError):
        raise error.orig from error
    raise error
