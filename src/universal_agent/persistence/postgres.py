from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, NoReturn, cast

from sqlalchemy import (
    URL,
    BigInteger,
    Column,
    DateTime,
    Engine,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy import insert as sql_insert
from sqlalchemy import select as sql_select
from sqlalchemy import update as sql_update
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError as SQLAlchemyIntegrityError
from sqlalchemy.schema import CreateTable

from universal_agent.core import AgentState, EventId, JsonMapping, RuntimeEvent, SessionId, utc_now
from universal_agent.core.config_validation import (
    parse_json_object,
    parse_non_empty_string,
    parse_positive_float,
    parse_positive_int,
)
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

POSTGRES_SCHEMA_VERSION = 1
POSTGRES_DEFAULT_TENANT_ID = "default"
POSTGRES_OUTBOX_PENDING = "pending"
POSTGRES_OUTBOX_PUBLISHING = "publishing"
POSTGRES_OUTBOX_PUBLISHED = "published"

_METADATA = MetaData()
_SCHEMA_MIGRATIONS = Table(
    "ua_schema_migrations",
    _METADATA,
    Column("version", Integer, primary_key=True),
    Column("name", String, nullable=False),
    Column("applied_at", DateTime(timezone=True), nullable=False),
)
_SESSIONS = Table(
    "ua_sessions",
    _METADATA,
    Column("tenant_id", String, nullable=False),
    Column("session_id", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSONB, nullable=False),
    PrimaryKeyConstraint("tenant_id", "session_id"),
)
_RUNTIME_EVENTS = Table(
    "ua_runtime_events",
    _METADATA,
    Column("sequence", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String, nullable=False),
    Column("event_id", String, nullable=False),
    Column("session_id", String, nullable=False),
    Column("goal_id", String, nullable=False),
    Column("task_id", String, nullable=False),
    Column("action_id", String, nullable=True),
    Column("type", String, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSONB, nullable=False),
    UniqueConstraint("tenant_id", "event_id", name="uq_ua_runtime_events_tenant_event"),
)
_RUNTIME_EVENT_OUTBOX = Table(
    "ua_runtime_event_outbox",
    _METADATA,
    Column("sequence", BigInteger, primary_key=True, autoincrement=True),
    Column("tenant_id", String, nullable=False),
    Column("event_id", String, nullable=False),
    Column("session_id", String, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("status", String, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("available_at", DateTime(timezone=True), nullable=False),
    Column("locked_by", String, nullable=True),
    Column("locked_until", DateTime(timezone=True), nullable=True),
    Column("published_at", DateTime(timezone=True), nullable=True),
    Column("last_error", String, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("tenant_id", "event_id", name="uq_ua_event_outbox_tenant_event"),
)
Index(
    "idx_ua_runtime_events_tenant_session_sequence",
    _RUNTIME_EVENTS.c.tenant_id,
    _RUNTIME_EVENTS.c.session_id,
    _RUNTIME_EVENTS.c.sequence,
)
Index(
    "idx_ua_event_outbox_pending",
    _RUNTIME_EVENT_OUTBOX.c.tenant_id,
    _RUNTIME_EVENT_OUTBOX.c.status,
    _RUNTIME_EVENT_OUTBOX.c.available_at,
    _RUNTIME_EVENT_OUTBOX.c.sequence,
)


@dataclass(frozen=True, slots=True)
class PostgresMigrationReport:
    current_version: int
    applied_versions: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PostgresOutboxEvent:
    sequence: int
    event_id: EventId
    session_id: SessionId
    event: RuntimeEvent
    attempts: int
    locked_by: str | None = None
    locked_until: datetime | None = None


class PostgresRuntimeStore:
    """Postgres-backed Session/Event store with transactional outbox support."""

    state_event_commit_strategy = "postgres_transactional_outbox"

    def __init__(
        self,
        url: str | URL | None = None,
        *,
        engine: Engine | None = None,
        tenant_id: str = POSTGRES_DEFAULT_TENANT_ID,
        auto_migrate: bool = True,
    ) -> None:
        if url is None and engine is None:
            raise ValueError("postgres runtime store requires a URL or engine")
        self._engine = engine if engine is not None else create_engine(cast(str | URL, url))
        self._tenant_id = parse_non_empty_string(tenant_id, "postgres tenant_id")
        if auto_migrate:
            self.migrate()

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def migrate(self) -> PostgresMigrationReport:
        return apply_postgres_migrations(self._engine)

    async def create_session(self, snapshot: SessionSnapshot) -> None:
        snapshot.version = 0
        timestamp = utc_now()
        with self._connect() as connection:
            try:
                connection.execute(
                    sql_insert(_SESSIONS).values(
                        tenant_id=self._tenant_id,
                        session_id=str(snapshot.state.session_id),
                        version=snapshot.version,
                        created_at=snapshot.state.goal.created_at,
                        updated_at=timestamp,
                        payload=encode_session_snapshot(snapshot),
                    )
                )
            except SQLAlchemyIntegrityError as exc:
                raise ValueError(f"session already exists: {snapshot.state.session_id}") from exc

    async def list_sessions(self) -> tuple[SessionSnapshot, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                sql_select(_SESSIONS.c.version, _SESSIONS.c.payload)
                .where(_SESSIONS.c.tenant_id == self._tenant_id)
                .order_by(_SESSIONS.c.created_at.desc(), _SESSIONS.c.session_id.desc())
            ).all()
        return tuple(_decode_session_row(cast(Mapping[str, Any], row._mapping)) for row in rows)

    async def load_session(self, session_id: SessionId) -> SessionSnapshot:
        with self._connect() as connection:
            return _load_stored_session(connection, self._tenant_id, session_id)

    async def save_session(self, snapshot: SessionSnapshot) -> None:
        original_version = snapshot.version
        snapshot.version = original_version + 1
        try:
            with self._connect() as connection:
                result = connection.execute(
                    sql_update(_SESSIONS)
                    .where(_SESSIONS.c.tenant_id == self._tenant_id)
                    .where(_SESSIONS.c.session_id == str(snapshot.state.session_id))
                    .where(_SESSIONS.c.version == original_version)
                    .values(
                        version=snapshot.version,
                        updated_at=utc_now(),
                        payload=encode_session_snapshot(snapshot),
                    )
                )
                if int(result.rowcount or 0) != 1:
                    snapshot.version = original_version
                    _raise_missing_or_conflict(
                        connection,
                        self._tenant_id,
                        snapshot.state.session_id,
                        original_version,
                    )
        except Exception:
            snapshot.version = original_version
            raise

    async def create(self, state: AgentState) -> None:
        await self.create_session(session_from_state(state))

    async def load(self, session_id: SessionId) -> AgentState:
        return (await self.load_session(session_id)).state

    async def save(self, state: AgentState) -> None:
        snapshot = await self.load_session(state.session_id)
        await self.save_session(with_state(snapshot, state))

    async def emit(self, event: RuntimeEvent) -> None:
        self.append(event)

    def append(self, event: RuntimeEvent) -> None:
        try:
            with self._connect() as connection:
                _insert_runtime_event(connection, self._tenant_id, event)
                _insert_runtime_event_outbox(connection, self._tenant_id, event)
        except SQLAlchemyIntegrityError:
            return

    def events_for(self, session_id: SessionId) -> tuple[RuntimeEvent, ...]:
        return tuple(event for event in self.all() if event.session_id == session_id)

    def all(self) -> tuple[RuntimeEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                sql_select(_RUNTIME_EVENTS.c.payload)
                .where(_RUNTIME_EVENTS.c.tenant_id == self._tenant_id)
                .order_by(_RUNTIME_EVENTS.c.sequence.asc())
            ).all()
        return tuple(decode_runtime_event(_json_object(row._mapping["payload"])) for row in rows)

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

    async def commit_session_event(
        self,
        snapshot: SessionSnapshot,
        event: RuntimeEvent,
    ) -> None:
        original_version = snapshot.version
        snapshot.version = original_version + 1
        try:
            with self._connect() as connection:
                result = connection.execute(
                    sql_update(_SESSIONS)
                    .where(_SESSIONS.c.tenant_id == self._tenant_id)
                    .where(_SESSIONS.c.session_id == str(snapshot.state.session_id))
                    .where(_SESSIONS.c.version == original_version)
                    .values(
                        version=snapshot.version,
                        updated_at=utc_now(),
                        payload=encode_session_snapshot(snapshot),
                    )
                )
                if int(result.rowcount or 0) != 1:
                    snapshot.version = original_version
                    _raise_missing_or_conflict(
                        connection,
                        self._tenant_id,
                        snapshot.state.session_id,
                        original_version,
                    )
                _insert_runtime_event(connection, self._tenant_id, event)
                _insert_runtime_event_outbox(connection, self._tenant_id, event)
        except Exception:
            snapshot.version = original_version
            raise

    def pending_outbox_events(self, *, limit: int | None = None) -> tuple[PostgresOutboxEvent, ...]:
        return self._outbox_events(
            status=POSTGRES_OUTBOX_PENDING,
            limit=limit,
            include_unlocked_only=True,
        )

    def lease_outbox_events(
        self,
        *,
        publisher_id: str,
        limit: int,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> tuple[PostgresOutboxEvent, ...]:
        parse_positive_int(limit, "outbox lease limit")
        parse_positive_float(ttl_seconds, "outbox lease ttl_seconds")
        publisher = parse_non_empty_string(publisher_id, "outbox publisher_id")
        timestamp = now or utc_now()
        locked_until = timestamp + timedelta(seconds=ttl_seconds)
        with self._connect() as connection:
            _reclaim_expired_outbox_leases(connection, self._tenant_id, timestamp)
            rows = connection.execute(
                sql_select(
                    _RUNTIME_EVENT_OUTBOX.c.sequence,
                    _RUNTIME_EVENT_OUTBOX.c.event_id,
                    _RUNTIME_EVENT_OUTBOX.c.session_id,
                    _RUNTIME_EVENT_OUTBOX.c.payload,
                    _RUNTIME_EVENT_OUTBOX.c.attempts,
                )
                .where(_RUNTIME_EVENT_OUTBOX.c.tenant_id == self._tenant_id)
                .where(_RUNTIME_EVENT_OUTBOX.c.status == POSTGRES_OUTBOX_PENDING)
                .where(_RUNTIME_EVENT_OUTBOX.c.available_at <= timestamp)
                .order_by(_RUNTIME_EVENT_OUTBOX.c.sequence.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            sequences = tuple(int(row._mapping["sequence"]) for row in rows)
            if sequences:
                connection.execute(
                    sql_update(_RUNTIME_EVENT_OUTBOX)
                    .where(_RUNTIME_EVENT_OUTBOX.c.tenant_id == self._tenant_id)
                    .where(_RUNTIME_EVENT_OUTBOX.c.sequence.in_(sequences))
                    .values(
                        status=POSTGRES_OUTBOX_PUBLISHING,
                        attempts=_RUNTIME_EVENT_OUTBOX.c.attempts + 1,
                        locked_by=publisher,
                        locked_until=locked_until,
                        last_error=None,
                    )
                )
        return self._outbox_events(
            status=POSTGRES_OUTBOX_PUBLISHING,
            event_ids=tuple(EventId(str(row._mapping["event_id"])) for row in rows),
        )

    def mark_outbox_published(
        self,
        event_ids: tuple[EventId, ...],
        *,
        publisher_id: str | None = None,
    ) -> int:
        if not event_ids:
            return 0
        statement = (
            sql_update(_RUNTIME_EVENT_OUTBOX)
            .where(_RUNTIME_EVENT_OUTBOX.c.tenant_id == self._tenant_id)
            .where(_RUNTIME_EVENT_OUTBOX.c.event_id.in_(tuple(str(item) for item in event_ids)))
            .where(_RUNTIME_EVENT_OUTBOX.c.status != POSTGRES_OUTBOX_PUBLISHED)
            .values(
                status=POSTGRES_OUTBOX_PUBLISHED,
                published_at=utc_now(),
                locked_by=None,
                locked_until=None,
                last_error=None,
            )
        )
        if publisher_id is not None:
            statement = statement.where(
                _RUNTIME_EVENT_OUTBOX.c.locked_by
                == parse_non_empty_string(publisher_id, "outbox publisher_id")
            )
        with self._connect() as connection:
            result = connection.execute(statement)
        return int(result.rowcount or 0)

    def release_outbox_events(
        self,
        event_ids: tuple[EventId, ...],
        *,
        publisher_id: str,
        reason: str,
        retry_at: datetime | None = None,
    ) -> int:
        if not event_ids:
            return 0
        publisher = parse_non_empty_string(publisher_id, "outbox publisher_id")
        error = parse_non_empty_string(reason, "outbox release reason")
        with self._connect() as connection:
            result = connection.execute(
                sql_update(_RUNTIME_EVENT_OUTBOX)
                .where(_RUNTIME_EVENT_OUTBOX.c.tenant_id == self._tenant_id)
                .where(_RUNTIME_EVENT_OUTBOX.c.event_id.in_(tuple(str(item) for item in event_ids)))
                .where(_RUNTIME_EVENT_OUTBOX.c.status == POSTGRES_OUTBOX_PUBLISHING)
                .where(_RUNTIME_EVENT_OUTBOX.c.locked_by == publisher)
                .values(
                    status=POSTGRES_OUTBOX_PENDING,
                    available_at=retry_at or utc_now(),
                    locked_by=None,
                    locked_until=None,
                    last_error=error,
                )
            )
        return int(result.rowcount or 0)

    def _outbox_events(
        self,
        *,
        status: str,
        limit: int | None = None,
        event_ids: tuple[EventId, ...] = (),
        include_unlocked_only: bool = False,
    ) -> tuple[PostgresOutboxEvent, ...]:
        if limit is not None:
            parse_positive_int(limit, "outbox limit")
        statement = (
            sql_select(
                _RUNTIME_EVENT_OUTBOX.c.sequence,
                _RUNTIME_EVENT_OUTBOX.c.event_id,
                _RUNTIME_EVENT_OUTBOX.c.session_id,
                _RUNTIME_EVENT_OUTBOX.c.payload,
                _RUNTIME_EVENT_OUTBOX.c.attempts,
                _RUNTIME_EVENT_OUTBOX.c.locked_by,
                _RUNTIME_EVENT_OUTBOX.c.locked_until,
            )
            .where(_RUNTIME_EVENT_OUTBOX.c.tenant_id == self._tenant_id)
            .where(_RUNTIME_EVENT_OUTBOX.c.status == status)
            .order_by(_RUNTIME_EVENT_OUTBOX.c.sequence.asc())
        )
        if event_ids:
            statement = statement.where(
                _RUNTIME_EVENT_OUTBOX.c.event_id.in_(tuple(str(item) for item in event_ids))
            )
        if include_unlocked_only:
            statement = statement.where(_RUNTIME_EVENT_OUTBOX.c.locked_by.is_(None))
        if limit is not None:
            statement = statement.limit(limit)
        with self._connect() as connection:
            rows = connection.execute(statement).all()
        return tuple(_decode_outbox_row(cast(Mapping[str, Any], row._mapping)) for row in rows)

    @contextmanager
    def _connect(self) -> Iterator[Connection]:
        with self._engine.begin() as connection:
            yield connection


def apply_postgres_migrations(engine: Engine) -> PostgresMigrationReport:
    timestamp = utc_now()
    with engine.begin() as connection:
        _METADATA.create_all(connection)
        existing = tuple(
            int(row._mapping["version"])
            for row in connection.execute(
                sql_select(_SCHEMA_MIGRATIONS.c.version).order_by(
                    _SCHEMA_MIGRATIONS.c.version.asc()
                )
            ).all()
        )
        if POSTGRES_SCHEMA_VERSION in existing:
            return PostgresMigrationReport(POSTGRES_SCHEMA_VERSION, ())
        connection.execute(
            sql_insert(_SCHEMA_MIGRATIONS).values(
                version=POSTGRES_SCHEMA_VERSION,
                name="initial_runtime_store",
                applied_at=timestamp,
            )
        )
        return PostgresMigrationReport(POSTGRES_SCHEMA_VERSION, (POSTGRES_SCHEMA_VERSION,))


def postgres_schema_table_names() -> tuple[str, ...]:
    return tuple(table.name for table in _METADATA.sorted_tables)


def postgres_schema_ddl() -> tuple[str, ...]:
    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    return tuple(
        str(CreateTable(table).compile(dialect=dialect)) for table in _METADATA.sorted_tables
    )


def _load_stored_session(
    connection: Connection,
    tenant_id: str,
    session_id: SessionId,
) -> SessionSnapshot:
    row = connection.execute(
        sql_select(_SESSIONS.c.version, _SESSIONS.c.payload)
        .where(_SESSIONS.c.tenant_id == tenant_id)
        .where(_SESSIONS.c.session_id == str(session_id))
    ).first()
    if row is None:
        raise StateNotFoundError(f"session not found: {session_id}")
    return _decode_session_row(cast(Mapping[str, Any], row._mapping))


def _decode_session_row(row: Mapping[str, Any]) -> SessionSnapshot:
    snapshot = decode_session_snapshot(_json_object(row["payload"]))
    snapshot.version = _int_column(row["version"])
    return snapshot


def _raise_missing_or_conflict(
    connection: Connection,
    tenant_id: str,
    session_id: SessionId,
    expected_version: int,
) -> NoReturn:
    row = connection.execute(
        sql_select(_SESSIONS.c.version)
        .where(_SESSIONS.c.tenant_id == tenant_id)
        .where(_SESSIONS.c.session_id == str(session_id))
    ).first()
    if row is None:
        raise StateNotFoundError(f"session not found: {session_id}")
    raise SessionVersionConflictError(
        f"session version conflict: {session_id} expected {row._mapping['version']}, "
        f"got {expected_version}"
    )


def _insert_runtime_event(connection: Connection, tenant_id: str, event: RuntimeEvent) -> None:
    connection.execute(
        sql_insert(_RUNTIME_EVENTS).values(
            tenant_id=tenant_id,
            event_id=str(event.id),
            session_id=str(event.session_id),
            goal_id=str(event.goal_id),
            task_id=str(event.task_id),
            action_id=None if event.action_id is None else str(event.action_id),
            type=event.type,
            occurred_at=event.occurred_at,
            payload=encode_runtime_event(event),
        )
    )


def _insert_runtime_event_outbox(
    connection: Connection,
    tenant_id: str,
    event: RuntimeEvent,
) -> None:
    timestamp = utc_now()
    connection.execute(
        sql_insert(_RUNTIME_EVENT_OUTBOX).values(
            tenant_id=tenant_id,
            event_id=str(event.id),
            session_id=str(event.session_id),
            payload=encode_runtime_event(event),
            status=POSTGRES_OUTBOX_PENDING,
            attempts=0,
            available_at=timestamp,
            locked_by=None,
            locked_until=None,
            published_at=None,
            last_error=None,
            created_at=timestamp,
        )
    )


def _reclaim_expired_outbox_leases(
    connection: Connection,
    tenant_id: str,
    timestamp: datetime,
) -> None:
    connection.execute(
        sql_update(_RUNTIME_EVENT_OUTBOX)
        .where(_RUNTIME_EVENT_OUTBOX.c.tenant_id == tenant_id)
        .where(_RUNTIME_EVENT_OUTBOX.c.status == POSTGRES_OUTBOX_PUBLISHING)
        .where(_RUNTIME_EVENT_OUTBOX.c.locked_until <= timestamp)
        .values(
            status=POSTGRES_OUTBOX_PENDING,
            available_at=timestamp,
            locked_by=None,
            locked_until=None,
            last_error="outbox lease expired",
        )
    )


def _decode_outbox_row(row: Mapping[str, Any]) -> PostgresOutboxEvent:
    return PostgresOutboxEvent(
        _int_column(row["sequence"]),
        EventId(str(row["event_id"])),
        SessionId(str(row["session_id"])),
        decode_runtime_event(_json_object(row["payload"])),
        _int_column(row["attempts"]),
        _optional_string(row["locked_by"]),
        _optional_datetime(row["locked_until"]),
    )


def _json_object(value: object) -> JsonMapping:
    return parse_json_object(value, "postgres payload")


def _int_column(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise TypeError(f"postgres integer column returned {type(value).__name__}")


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    raise TypeError(f"postgres datetime column returned {type(value).__name__}")
