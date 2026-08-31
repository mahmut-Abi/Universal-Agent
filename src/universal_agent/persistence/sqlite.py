from __future__ import annotations

import sqlite3
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from sqlalchemy import (
    URL,
    Column,
    Engine,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
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
from universal_agent.core.config_validation import parse_json_object
from universal_agent.persistence.codec import (
    decode_runtime_event,
    decode_session_snapshot,
    encode_runtime_event,
    encode_session_snapshot,
)
from universal_agent.runtime.events import filter_events, poll_event_reader
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
    """SQLite-backed SessionStore adapter for local durable runtime deployments."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._engine: Engine | None = None

    async def create_session(self, snapshot: SessionSnapshot) -> None:
        snapshot.version = 0
        payload = _encode_json(encode_session_snapshot(snapshot))
        with self._connect() as connection:
            try:
                connection.execute(
                    sql_insert(_SESSIONS).values(
                        session_id=str(snapshot.state.session_id),
                        created_at=snapshot.state.goal.created_at.isoformat(),
                        payload=payload,
                    ),
                )
            except SQLAlchemyIntegrityError as exc:
                raise ValueError(f"session already exists: {snapshot.state.session_id}") from exc

    async def list_sessions(self) -> tuple[SessionSnapshot, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                sql_select(_SESSIONS.c.payload).order_by(
                    _SESSIONS.c.created_at.desc(),
                    _SESSIONS.c.session_id.desc(),
                )
            ).all()
        return tuple(decode_session_snapshot(_loads_json_object(row[0])) for row in rows)

    async def load_session(self, session_id: SessionId) -> SessionSnapshot:
        with self._connect() as connection:
            return _load_stored_session(connection, session_id)

    async def save_session(self, snapshot: SessionSnapshot) -> None:
        with self._connect() as connection:
            stored = _load_stored_session(connection, snapshot.state.session_id)
            if snapshot.version != stored.version:
                raise SessionVersionConflictError(
                    f"session version conflict: {snapshot.state.session_id} expected "
                    f"{stored.version}, got {snapshot.version}"
                )
            snapshot.version = stored.version + 1
            payload = _encode_json(encode_session_snapshot(snapshot))
            connection.execute(
                sql_update(_SESSIONS)
                .where(_SESSIONS.c.session_id == str(snapshot.state.session_id))
                .values(
                    created_at=snapshot.state.goal.created_at.isoformat(),
                    payload=payload,
                ),
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
            self._engine = create_engine(URL.create("sqlite", database=str(self._path)))
            _METADATA.create_all(self._engine)
        return self._engine


class SQLiteEventStore:
    """SQLite-backed EventSink/EventReader adapter with cursor-compatible ordering."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._engine: Engine | None = None

    async def emit(self, event: RuntimeEvent) -> None:
        self.append(event)

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
        events = tuple(
            event
            for event in (self.all() if session_id is None else self.events_for(session_id))
            if event.type != SESSION_STATE_EVENT
        )
        return filter_events(
            events,
            session_id=session_id,
            after_event_id=after_event_id,
            limit=limit,
        )

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
                int(row[0]),
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
        return int(result.rowcount or 0)

    @contextmanager
    def _connect(self) -> Iterator[Connection]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._sqlite_engine().begin() as connection:
            yield connection

    def _sqlite_engine(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine(URL.create("sqlite", database=str(self._path)))
            _METADATA.create_all(self._engine)
        return self._engine


class SQLiteRuntimeStore(SQLiteSessionStore, SQLiteEventStore):
    """SQLite adapter that can commit a SessionSnapshot and RuntimeEvent atomically."""

    state_event_commit_strategy = "sqlite_transaction"

    async def commit_session_event(
        self,
        snapshot: SessionSnapshot,
        event: RuntimeEvent,
    ) -> None:
        original_version = snapshot.version
        with self._connect() as connection:
            stored = _load_stored_session(connection, snapshot.state.session_id)
            if snapshot.version != stored.version:
                raise SessionVersionConflictError(
                    f"session version conflict: {snapshot.state.session_id} expected "
                    f"{stored.version}, got {snapshot.version}"
                )
            snapshot.version = stored.version + 1
            try:
                connection.execute(
                    sql_update(_SESSIONS)
                    .where(_SESSIONS.c.session_id == str(snapshot.state.session_id))
                    .values(
                        created_at=snapshot.state.goal.created_at.isoformat(),
                        payload=_encode_json(encode_session_snapshot(snapshot)),
                    ),
                )
                _insert_runtime_event(connection, event)
                _insert_runtime_event_outbox(connection, event)
            except SQLAlchemyIntegrityError as exc:
                snapshot.version = original_version
                _raise_sqlite_integrity_error(exc)
            except Exception:
                snapshot.version = original_version
                raise


def _encode_json(payload: object) -> str:
    return dumps_json(payload)


def _loads_json_object(value: str | bytes | bytearray) -> JsonMapping:
    return parse_json_object(loads_json(value), "sqlite payload")


def _load_stored_session(connection: Connection, session_id: SessionId) -> SessionSnapshot:
    row = connection.execute(
        sql_select(_SESSIONS.c.payload).where(_SESSIONS.c.session_id == str(session_id))
    ).first()
    if row is None:
        raise StateNotFoundError(f"session not found: {session_id}")
    return decode_session_snapshot(_loads_json_object(row[0]))


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
