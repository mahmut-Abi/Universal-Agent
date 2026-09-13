from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from universal_agent.core import (
    AgentState,
    EventId,
    Goal,
    RuntimeEvent,
    SessionId,
    SuccessCriterion,
    Task,
    TaskId,
)
from universal_agent.persistence import FileSessionStore, SQLiteEventStore, SQLiteSessionStore
from universal_agent.runtime.events import EventCursorError
from universal_agent.state import InMemorySessionStore, session_from_state
from universal_agent.state.store import SessionStore

SESSION_IDS = ("session-1", "session-2", "session-3", "session-4", "session-5")


def make_state(session_id: str, index: int) -> AgentState:
    goal = Goal(
        "verify deployment",
        (SuccessCriterion("healthy", True),),
        created_at=datetime(2026, 1, 1 + index, tzinfo=UTC),
    )
    task = Task("probe", ("healthy",), TaskId("task-1"))
    return AgentState(SessionId(session_id), goal, task)


def make_event(session_id: str, event_type: str, event_id: str) -> RuntimeEvent:
    snapshot = session_from_state(make_state(session_id, 1))
    return RuntimeEvent(
        event_type,
        SessionId(session_id),
        snapshot.state.goal.id,
        snapshot.state.current_task.id,
        EventId(event_id),
    )


async def make_store(store_kind: str, tmp_path: Path) -> SessionStore:
    store: SessionStore
    if store_kind == "memory":
        store = InMemorySessionStore()
    elif store_kind == "file":
        store = FileSessionStore(tmp_path / "file-store")
    else:
        store = SQLiteSessionStore(tmp_path / "runtime.sqlite3")
    for index, session_id in enumerate(SESSION_IDS):
        await store.create_session(session_from_state(make_state(session_id, index)))
    return store


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["memory", "file", "sqlite"])
async def test_list_sessions_orders_newest_first(store_kind: str, tmp_path: Path) -> None:
    store = await make_store(store_kind, tmp_path)

    sessions = await store.list_sessions()

    assert [str(item.state.session_id) for item in sessions] == list(reversed(SESSION_IDS))


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["memory", "file", "sqlite"])
async def test_list_sessions_limit_returns_newest_window(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store = await make_store(store_kind, tmp_path)

    sessions = await store.list_sessions(limit=2)

    assert [str(item.state.session_id) for item in sessions] == ["session-5", "session-4"]


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["memory", "file", "sqlite"])
async def test_list_sessions_after_cursor_skips_seen_sessions(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store = await make_store(store_kind, tmp_path)

    sessions = await store.list_sessions(after_session_id=SessionId("session-4"), limit=2)

    assert [str(item.state.session_id) for item in sessions] == ["session-3", "session-2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["memory", "file", "sqlite"])
async def test_list_sessions_cursor_at_oldest_returns_empty(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store = await make_store(store_kind, tmp_path)

    sessions = await store.list_sessions(after_session_id=SessionId("session-1"))

    assert sessions == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["memory", "file", "sqlite"])
async def test_list_sessions_unknown_cursor_raises(store_kind: str, tmp_path: Path) -> None:
    store = await make_store(store_kind, tmp_path)

    with pytest.raises(ValueError, match="session cursor not found"):
        await store.list_sessions(after_session_id=SessionId("session-missing"))


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["memory", "file", "sqlite"])
async def test_list_sessions_rejects_non_positive_limit(
    store_kind: str,
    tmp_path: Path,
) -> None:
    store = await make_store(store_kind, tmp_path)

    with pytest.raises(ValueError, match="session list limit must be positive"):
        await store.list_sessions(limit=0)


@pytest.mark.asyncio
async def test_sqlite_event_store_limit_and_cursor_use_pushdown(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    for index in range(5):
        await store.emit(make_event("session-1", "StateUpdated", f"event-{index}"))
    await store.emit(make_event("session-2", "StateUpdated", "event-other"))

    window = await store.list_events(
        SessionId("session-1"),
        after_event_id=EventId("event-2"),
        limit=2,
    )
    assert [event.id for event in window] == [EventId("event-3"), EventId("event-4")]

    no_cursor = await store.list_events(SessionId("session-1"), limit=1)
    assert [event.id for event in no_cursor] == [EventId("event-0")]

    other_session = await store.list_events(SessionId("session-2"))
    assert [event.id for event in other_session] == [EventId("event-other")]


@pytest.mark.asyncio
async def test_sqlite_event_store_unknown_cursor_raises(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    await store.emit(make_event("session-1", "StateUpdated", "event-1"))

    with pytest.raises(EventCursorError, match="event cursor not found"):
        await store.list_events(SessionId("session-1"), after_event_id=EventId("event-missing"))


@pytest.mark.asyncio
async def test_sqlite_event_store_cursor_from_other_session_raises(tmp_path: Path) -> None:
    """A cursor outside the queried session is not in scope, matching filter_events."""
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    await store.emit(make_event("session-1", "StateUpdated", "event-1"))
    await store.emit(make_event("session-2", "StateUpdated", "event-2"))

    with pytest.raises(EventCursorError, match="event cursor not found"):
        await store.list_events(SessionId("session-2"), after_event_id=EventId("event-1"))


@pytest.mark.asyncio
async def test_sqlite_event_store_rejects_non_positive_limit(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    await store.emit(make_event("session-1", "StateUpdated", "event-1"))

    with pytest.raises(ValueError, match="event stream limit must be positive"):
        await store.list_events(SessionId("session-1"), limit=0)
