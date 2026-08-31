from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from universal_agent import (
    InMemoryEventSink,
)
from universal_agent.core import (
    EventId,
    GoalId,
    RuntimeEvent,
    SessionId,
    TaskId,
    utc_now,
)
from universal_agent.persistence import FileEventStore, SQLiteEventStore
from universal_agent.runtime.events import filter_events


def _make_event(
    session_id: SessionId,
    event_type: str = "GoalCreated",
    goal_id: GoalId | None = None,
    task_id: TaskId | None = None,
) -> RuntimeEvent:
    return RuntimeEvent(
        type=event_type,
        session_id=session_id,
        goal_id=goal_id or GoalId("g1"),
        task_id=task_id or TaskId("t1"),
        id=EventId(f"evt-{event_type.lower()}"),
        data={},
        occurred_at=utc_now(),
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_watch_events_yields_emitted_events() -> None:
    sink = InMemoryEventSink()
    session = SessionId("s1")
    received: list[RuntimeEvent] = []

    async def _consume() -> None:
        async for event in sink.watch_events(session, heartbeat_interval=0.1):
            received.append(event)
            if len(received) >= 2:
                break

    async def _produce() -> None:
        await asyncio.sleep(0.05)
        await sink.emit(_make_event(session, "GoalCreated"))
        await asyncio.sleep(0.05)
        await sink.emit(_make_event(session, "DecisionGenerated"))

    await asyncio.gather(_consume(), _produce())
    types = [e.type for e in received]
    assert "GoalCreated" in types
    assert "DecisionGenerated" in types


@pytest.mark.asyncio
@pytest.mark.unit
async def test_watch_events_heartbeat_on_idle() -> None:
    sink = InMemoryEventSink()
    session = SessionId("s1")
    received: list[RuntimeEvent] = []

    async for event in sink.watch_events(session, heartbeat_interval=0.05):
        received.append(event)
        if event.type == "Heartbeat":
            break

    assert any(e.type == "Heartbeat" for e in received)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_watch_events_after_cursor() -> None:
    sink = InMemoryEventSink()
    session = SessionId("s1")
    e1 = _make_event(session, "GoalCreated")
    e2 = _make_event(session, "DecisionGenerated")
    await sink.emit(e1)
    await sink.emit(e2)

    received: list[RuntimeEvent] = []
    async for event in sink.watch_events(session, after_event_id=e1.id, heartbeat_interval=0.05):
        received.append(event)
        if len(received) >= 1:
            break

    types = [e.type for e in received]
    assert "DecisionGenerated" in types
    assert "GoalCreated" not in types


@pytest.mark.asyncio
@pytest.mark.unit
async def test_watch_events_cleans_up_subscriber() -> None:
    sink = InMemoryEventSink()
    session = SessionId("s1")
    assert len(sink._subscribers) == 0

    gen = sink.watch_events(session, heartbeat_interval=0.05)
    async for _event in gen:
        break
    await gen.aclose()
    assert len(sink._subscribers) == 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_file_event_store_watch_events_polls_new_events(tmp_path: Path) -> None:
    store = FileEventStore(tmp_path)
    session = SessionId("s1")
    received: list[RuntimeEvent] = []

    async def _consume() -> None:
        async for event in store.watch_events(session, heartbeat_interval=0.01):
            received.append(event)
            if event.type == "GoalCreated":
                break

    async def _produce() -> None:
        await asyncio.sleep(0.02)
        await store.emit(_make_event(session, "GoalCreated"))

    await asyncio.gather(_consume(), _produce())

    assert [event.type for event in received] == ["Heartbeat", "GoalCreated"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_sqlite_event_store_watch_events_polls_new_events(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    session = SessionId("s1")
    received: list[RuntimeEvent] = []

    async def _consume() -> None:
        async for event in store.watch_events(session, heartbeat_interval=0.01):
            received.append(event)
            if event.type == "DecisionGenerated":
                break

    async def _produce() -> None:
        await asyncio.sleep(0.02)
        await store.emit(_make_event(session, "DecisionGenerated"))

    await asyncio.gather(_consume(), _produce())

    assert [event.type for event in received] == ["Heartbeat", "DecisionGenerated"]


@pytest.mark.unit
def test_filter_events_basic() -> None:
    session = SessionId("s1")
    e1 = _make_event(session, "GoalCreated")
    e2 = _make_event(session, "DecisionGenerated")
    result = filter_events([e1, e2], session_id=session)
    assert len(result) == 2


@pytest.mark.unit
def test_filter_events_after_cursor() -> None:
    session = SessionId("s1")
    e1 = _make_event(session, "GoalCreated")
    e2 = _make_event(session, "DecisionGenerated")
    result = filter_events([e1, e2], after_event_id=e1.id)
    assert len(result) == 1
    assert result[0].type == "DecisionGenerated"


@pytest.mark.unit
def test_filter_events_limit() -> None:
    session = SessionId("s1")
    events = [_make_event(session, f"Event{i}") for i in range(5)]
    result = filter_events(events, limit=3)
    assert len(result) == 3


@pytest.mark.unit
def test_filter_events_session_scoped() -> None:
    e1 = _make_event(SessionId("s1"), "GoalCreated")
    e2 = _make_event(SessionId("s2"), "DecisionGenerated")
    result = filter_events([e1, e2], session_id=SessionId("s1"))
    assert len(result) == 1
    assert result[0].session_id == SessionId("s1")
