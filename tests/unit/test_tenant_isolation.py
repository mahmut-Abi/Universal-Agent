"""Cross-tenant isolation matrix (Phase 0 acceptance core).

Two ``PostgresRuntimeStore`` instances on the same database but different
tenants must be fully isolated across create / load / save / list / events /
outbox. This is a live integration test (skipped unless a Postgres DSN is set —
see `docker compose --profile postgres`).

See docs/phase0-principal-implementation.md §5.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import create_engine, make_url, text

from universal_agent.core import (
    AgentState,
    Goal,
    GoalId,
    RuntimeEvent,
    SessionId,
    Task,
    TaskId,
)
from universal_agent.persistence.postgres import PostgresRuntimeStore
from universal_agent.state import StateNotFoundError, session_from_state


def _require_pg() -> str:
    dsn = os.environ.get("UA_TEST_PG_URL") or os.environ.get("AGENTD_PG_URL")
    if not dsn:
        pytest.skip("no Postgres DSN set (UA_TEST_PG_URL or AGENTD_PG_URL)")
    return dsn


def _fresh_database(admin_dsn: str) -> str:
    url = make_url(admin_dsn)
    dbname = f"ua_test_iso_{uuid.uuid4().hex[:8]}"
    admin = create_engine(admin_dsn, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {dbname}"))
    admin.dispose()
    return str(url.set(database=dbname))


def _drop_database(admin_dsn: str, target: str) -> None:
    dbname = make_url(target).database
    admin = create_engine(admin_dsn, isolation_level="AUTOCOMMIT")
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


def _make_event(session_id: str) -> RuntimeEvent:
    return RuntimeEvent(
        type="test.event",
        session_id=SessionId(session_id),
        goal_id=GoalId("g1"),
        task_id=TaskId("t1"),
    )


@pytest.mark.integration
def test_cross_tenant_access_matrix() -> None:
    dsn = _require_pg()
    target = _fresh_database(dsn)
    t1: PostgresRuntimeStore | None = None
    t2: PostgresRuntimeStore | None = None
    try:
        t1 = PostgresRuntimeStore(url=target, tenant_id="t1")
        t2 = PostgresRuntimeStore(url=target, tenant_id="t2")

        # --- create isolation -------------------------------------------------
        asyncio.run(t1.create_session(session_from_state(_make_state("s-1"))))

        # t2 must not see t1's session, by load ...
        async def _load_cross() -> str:
            with pytest.raises(StateNotFoundError):
                await t2.load_session(SessionId("s-1"))
            loaded = await t1.load_session(SessionId("s-1"))
            return str(loaded.state.session_id)

        assert asyncio.run(_load_cross()) == "s-1"

        # ... nor by listing.
        assert [s.state.session_id for s in asyncio.run(t1.list_sessions())] == ["s-1"]
        assert [s.state.session_id for s in asyncio.run(t2.list_sessions())] == []

        # --- events isolation -------------------------------------------------
        evt = _make_event("s-1")
        t1.append(evt)
        assert str(evt.id) in {str(e.id) for e in t1.all()}
        assert str(evt.id) not in {str(e.id) for e in t2.all()}
        assert t2.events_for(SessionId("s-1")) == ()

        # --- outbox isolation -------------------------------------------------
        assert str(evt.id) in {e.event_id for e in t1.pending_outbox_events()}
        assert str(evt.id) not in {e.event_id for e in t2.pending_outbox_events()}
    finally:
        if t1 is not None:
            t1._engine.dispose()
        if t2 is not None:
            t2._engine.dispose()
        _drop_database(dsn, target)
