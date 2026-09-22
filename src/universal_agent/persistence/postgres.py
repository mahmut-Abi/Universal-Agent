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
    Float,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    text,
    tuple_,
)
from sqlalchemy import delete as sql_delete
from sqlalchemy import insert as sql_insert
from sqlalchemy import select as sql_select
from sqlalchemy import update as sql_update
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError as SQLAlchemyIntegrityError
from sqlalchemy.schema import CreateTable

from universal_agent.core import (
    DEFAULT_TENANT_ID,
    AgentState,
    EventId,
    JsonMapping,
    RuntimeEvent,
    SessionId,
    dumps_json,
    immutable_json,
    loads_json,
    utc_now,
)
from universal_agent.core.config_validation import (
    parse_json_object,
    parse_non_empty_string,
    parse_positive_float,
    parse_positive_int,
)
from universal_agent.eventstream import filter_events, poll_event_reader
from universal_agent.memory import MemoryId, MemoryKind, MemoryQuery, MemoryRecord
from universal_agent.persistence.codec import (
    decode_runtime_event,
    decode_session_snapshot,
    encode_runtime_event,
    encode_session_snapshot,
)
from universal_agent.state import (
    SessionSnapshot,
    SessionVersionConflictError,
    StateNotFoundError,
    session_from_state,
)
from universal_agent.state.event_store import SESSION_STATE_EVENT
from universal_agent.state.session import with_state

POSTGRES_SCHEMA_VERSION = 4
POSTGRES_DEFAULT_TENANT_ID = DEFAULT_TENANT_ID
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
    Column("user_id", String, nullable=False, server_default="system"),
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


# --- Principal / identity tables (schema v3, Phase 1) --------------------
# These are brand-new tables; ``create_all`` builds them, so migration step 3
# is a no-op that simply records the version. They underpin the credential -
# > principal -> RBAC resolution added in Phase 1.

_UA_USERS = Table(
    "ua_users",
    _METADATA,
    Column("user_id", String, primary_key=True),
    Column("email", String, nullable=False, unique=True),
    Column("display_name", String),
    Column("status", String, nullable=False, server_default="active"),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

_UA_TENANTS = Table(
    "ua_tenants",
    _METADATA,
    Column("tenant_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("status", String, nullable=False, server_default="active"),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

_UA_TENANT_MEMBERSHIPS = Table(
    "ua_tenant_memberships",
    _METADATA,
    Column("tenant_id", String, nullable=False),
    Column("user_id", String, nullable=False),
    Column("role", String, nullable=False, server_default="operator"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("tenant_id", "user_id"),
)

_UA_CREDENTIALS = Table(
    "ua_credentials",
    _METADATA,
    Column("credential_id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("user_id", String, nullable=False),
    # Only the hash of a credential is ever stored; the raw token never is.
    Column("token_hash", String, nullable=False, unique=True),
    Column("scope", String, nullable=False, server_default="read_write"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
)

# Operator/domain memories (schema v4): tenant-scoped like every ua_* table.
# The tenant comes from the record metadata stamped by the agentd data-plane
# layer; records without one land in the default tenant.
_UA_MEMORIES = Table(
    "ua_memories",
    _METADATA,
    Column("tenant_id", String, nullable=False, server_default="default"),
    Column("memory_id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("subject", String, nullable=False),
    Column("content", Text, nullable=False),
    Column("scope", String, nullable=False, server_default=""),
    Column("confidence", Float, nullable=False),
    Column("source_session_id", String, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("tags", Text, nullable=False, server_default="[]"),
    Column("source", String, nullable=False, server_default=""),
    Column("metadata", Text, nullable=False, server_default="{}"),
)
Index(
    "idx_ua_memories_tenant_created",
    _UA_MEMORIES.c.tenant_id,
    _UA_MEMORIES.c.created_at,
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
        user_id: str = "system",
        auto_migrate: bool = True,
    ) -> None:
        if url is None and engine is None:
            raise ValueError("postgres runtime store requires a URL or engine")
        self._engine = engine if engine is not None else create_engine(cast(str | URL, url))
        self._tenant_id = parse_non_empty_string(tenant_id, "postgres tenant_id")
        self._user_id = parse_non_empty_string(user_id, "postgres user_id")
        if auto_migrate:
            self.migrate()

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def user_id(self) -> str:
        return self._user_id

    def migrate(self) -> PostgresMigrationReport:
        return apply_postgres_migrations(self._engine)

    async def create_session(self, snapshot: SessionSnapshot) -> None:
        snapshot.version = 0
        timestamp = utc_now()
        with self._connect() as connection:
            try:
                # pi-lens-ignore: python-sql-injection
                connection.execute(
                    sql_insert(_SESSIONS).values(
                        tenant_id=self._tenant_id,
                        session_id=str(snapshot.state.session_id),
                        version=snapshot.version,
                        created_at=snapshot.state.goal.created_at,
                        updated_at=timestamp,
                        payload=encode_session_snapshot(snapshot),
                        user_id=self._user_id,
                    )
                )
            except SQLAlchemyIntegrityError as exc:
                raise ValueError(f"session already exists: {snapshot.state.session_id}") from exc

    async def list_sessions(
        self,
        *,
        after_session_id: SessionId | None = None,
        limit: int | None = None,
    ) -> tuple[SessionSnapshot, ...]:
        if limit is not None and limit < 1:
            raise ValueError("session list limit must be positive")
        with self._connect() as connection:
            statement = (
                sql_select(_SESSIONS.c.version, _SESSIONS.c.payload)
                .where(_SESSIONS.c.tenant_id == self._tenant_id)
                .order_by(_SESSIONS.c.created_at.desc(), _SESSIONS.c.session_id.desc())
            )
            if after_session_id is not None:
                cursor_key = self._session_cursor_key(connection, after_session_id)
                statement = statement.where(
                    tuple_(_SESSIONS.c.created_at, _SESSIONS.c.session_id) < cursor_key
                )
            if limit is not None:
                statement = statement.limit(limit)
            # pi-lens-ignore: python-sql-injection
            rows = connection.execute(statement).all()
        return tuple(_decode_session_row(cast(Mapping[str, Any], row._mapping)) for row in rows)

    def _session_cursor_key(
        self,
        connection: Connection,
        session_id: SessionId,
    ) -> tuple[datetime, str]:
        """Resolve the (created_at, session_id) ordering key of the cursor.

        Sessions strictly after the cursor in newest-first order are exactly
        those whose ordering key is smaller than the cursor's key.
        """
        # pi-lens-ignore: python-sql-injection
        row = connection.execute(
            sql_select(_SESSIONS.c.created_at, _SESSIONS.c.session_id)
            .where(_SESSIONS.c.tenant_id == self._tenant_id)
            .where(_SESSIONS.c.session_id == str(session_id))
        ).first()
        if row is None:
            raise ValueError(f"session cursor not found: {session_id}")
        created_at = row[0]
        if not isinstance(created_at, datetime):
            raise ValueError(f"invalid stored created_at for session {session_id}")
        return (created_at, str(row[1]))

    async def load_session(self, session_id: SessionId) -> SessionSnapshot:
        with self._connect() as connection:
            return _load_stored_session(connection, self._tenant_id, session_id)

    async def save_session(self, snapshot: SessionSnapshot) -> None:
        original_version = snapshot.version
        snapshot.version = original_version + 1
        try:
            with self._connect() as connection:
                # pi-lens-ignore: python-sql-injection
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
            # pi-lens-ignore: python-sql-injection
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
                # pi-lens-ignore: python-sql-injection
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
            # pi-lens-ignore: python-sql-injection
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
            sequences = tuple(_int_column(row._mapping["sequence"]) for row in rows)
            if sequences:
                # pi-lens-ignore: python-sql-injection
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
            # pi-lens-ignore: python-sql-injection
            result = connection.execute(statement)
        return result.rowcount or 0

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
        return result.rowcount or 0

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


class PostgresMemoryStore:
    """Tenant-scoped Postgres adapter for operator/domain memories.

    The tenant comes from the record metadata stamped by the agentd
    data-plane layer; records without one land in the default tenant.
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        engine: Engine | None = None,
    ) -> None:
        if url is None and engine is None:
            raise ValueError("postgres memory store requires a URL or engine")
        self._engine = engine if engine is not None else create_engine(url)  # type: ignore[arg-type]
        apply_postgres_migrations(self._engine)

    @contextmanager
    def _connect(self) -> Iterator[Connection]:
        with self._engine.begin() as connection:
            yield connection

    def add(self, record: MemoryRecord) -> bool:
        tenant = _memory_tenant(record)
        with self._connect() as connection:
            existing = connection.execute(
                sql_select(_UA_MEMORIES.c.memory_id)
                .where(_UA_MEMORIES.c.tenant_id == tenant)
                .where(_UA_MEMORIES.c.memory_id == str(record.id))
            ).first()
            if existing is not None:
                return False
            connection.execute(
                sql_insert(_UA_MEMORIES).values(_memory_row(record, tenant)),
            )
            return True

    def get(self, memory_id: MemoryId) -> MemoryRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                sql_select(_UA_MEMORIES).where(_UA_MEMORIES.c.memory_id == str(memory_id))
            ).first()
        if row is None:
            return None
        return _memory_record(dict(row._mapping))

    def delete(self, memory_id: MemoryId) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                sql_delete(_UA_MEMORIES).where(_UA_MEMORIES.c.memory_id == str(memory_id))
            )
        return result.rowcount > 0

    def export(self) -> tuple[MemoryRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                sql_select(_UA_MEMORIES).order_by(
                    _UA_MEMORIES.c.created_at, _UA_MEMORIES.c.memory_id
                )
            ).mappings()
            records = tuple(_memory_record(dict(row)) for row in rows)
        return records

    def query(self, query: MemoryQuery) -> tuple[MemoryRecord, ...]:
        matches = [
            record
            for record in self.export()
            if not query.kinds or record.kind in query.kinds
            if not query.subjects or record.subject in query.subjects
            if query.scope is None or record.scope == query.scope or record.scope == ""
        ]
        matches.sort(
            key=lambda item: (item.created_at, str(item.id)),
            reverse=query.limit is not None,
        )
        if query.limit is not None:
            matches = matches[: query.limit]
        return tuple(matches)


def apply_postgres_migrations(engine: Engine) -> PostgresMigrationReport:
    """Apply pending schema migrations in version order and report what ran.

    Table/materialized ``_METADATA`` definition drives CREATE for missing
    tables, but **never ALTERs existing tables**. Schema shape changes on
    existing tables must therefore be real DDL steps (see v2 below). Steps are
    recorded per-version in ``ua_schema_migrations``, so re-running is idempotent.
    """

    timestamp = utc_now()
    applied: list[int] = []
    with engine.begin() as connection:
        _METADATA.create_all(connection)
        existing = {
            _int_column(row._mapping["version"])
            for row in connection.execute(sql_select(_SCHEMA_MIGRATIONS.c.version)).all()
        }
        for version in range(1, POSTGRES_SCHEMA_VERSION + 1):
            if version in existing:
                continue
            _apply_migration_step(connection, version)
            connection.execute(
                sql_insert(_SCHEMA_MIGRATIONS).values(
                    version=version,
                    name=_MIGRATION_NAMES.get(version, f"migration_v{version}"),
                    applied_at=timestamp,
                )
            )
            applied.append(version)
    return PostgresMigrationReport(POSTGRES_SCHEMA_VERSION, tuple(applied))


_MIGRATION_NAMES = {
    1: "initial_runtime_store",
    2: "session_user_id",
    3: "security_principals",
}


def _apply_migration_step(connection: Connection, version: int) -> None:
    """Idempotent per-version DDL that shape-changes existing tables.

    v1 was the original store creation; it has no incremental DDL because
    ``_METADATA.create_all`` already guarantees its tables exist.
    """

    if version == 2:
        # ua_sessions gained a NOT NULL ownership column (Phase 0). The
        # server_default backfills every legacy row to 'system'; IF NOT EXISTS
        # keeps the step a no-op when the table was freshly created from the
        # updated _SESSIONS metadata definition.
        connection.execute(
            text(
                "ALTER TABLE ua_sessions "
                "ADD COLUMN IF NOT EXISTS "
                "user_id VARCHAR NOT NULL DEFAULT 'system'"
            )
        )
    # v3 adds brand-new principal tables (ua_users, ua_tenants,
    # ua_tenant_memberships, ua_credentials). They are constructed by
    # create_all, so step 3 needs no extra DDL here.
    # v4 adds ua_memories (operator/domain memories, tenant-scoped) — also
    # constructed by create_all; step 4 only records the version.


def postgres_schema_table_names() -> tuple[str, ...]:
    return tuple(table.name for table in _METADATA.sorted_tables)


def postgres_memory_tables() -> tuple[Table, ...]:
    """The (memories) operator-memory table for the memory store implementation."""

    return (_UA_MEMORIES,)


def postgres_principal_tables() -> tuple[Table, Table, Table, Table]:
    """The (users, tenants, memberships, credentials) principal tables.

    Public accessor so the credential store implementation can share this
    exact DDL without duplicating it (or importing private names).
    """

    return _UA_USERS, _UA_TENANTS, _UA_TENANT_MEMBERSHIPS, _UA_CREDENTIALS


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


def _memory_tenant(record: MemoryRecord) -> str:
    tenant = record.metadata.get("tenant_id")
    return (
        str(tenant) if isinstance(tenant, str) and tenant.strip() else (POSTGRES_DEFAULT_TENANT_ID)
    )


def _memory_row(record: MemoryRecord, tenant_id: str) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "memory_id": str(record.id),
        "kind": record.kind.value,
        "subject": record.subject,
        "content": record.content,
        "scope": record.scope,
        "confidence": record.confidence,
        "source_session_id": (
            None if record.source_session_id is None else str(record.source_session_id)
        ),
        "created_at": record.created_at,
        "version": record.version,
        "tags": dumps_json(list(record.tags)),
        "source": record.source,
        "metadata": dumps_json(dict(record.metadata)),
    }


def _memory_record(row: Mapping[str, Any]) -> MemoryRecord:
    created_at = row.get("created_at")
    parsed_created_at = created_at if isinstance(created_at, datetime) else utc_now()
    tags_value = row.get("tags")
    tags = loads_json(str(tags_value)) if isinstance(tags_value, str) else []
    metadata_value = row.get("metadata")
    metadata = loads_json(str(metadata_value)) if isinstance(metadata_value, str) else {}
    kind = row.get("kind")
    del metadata_value
    source_session = row.get("source_session_id")
    tenant_id_row = row.get("tenant_id")
    return MemoryRecord(
        kind=MemoryKind(str(kind)) if isinstance(kind, str) else MemoryKind.SEMANTIC,
        subject=str(row.get("subject", "")),
        content=str(row.get("content", "")),
        scope=str(row.get("scope", "")),
        confidence=float(row.get("confidence", 1.0)),
        source_session_id=SessionId(str(source_session)) if source_session is not None else None,
        id=MemoryId(str(row.get("memory_id", ""))),
        created_at=parsed_created_at,
        version=int(row.get("version", 1)),
        tags=tuple(str(item) for item in tags) if isinstance(tags, list) else (),
        source=str(row.get("source", "")),
        metadata=immutable_json(metadata if isinstance(metadata, dict) else {}),
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
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, str | bytes | bytearray):
            try:
                return int(value)
            except ValueError as exc:
                raise TypeError("postgres integer column returned non-integer text") from exc
        raise TypeError(f"postgres integer column returned {type(value).__name__}")
    return value


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
