from __future__ import annotations

import asyncio

import pytest

from universal_agent.core import (
    AgentState,
    Goal,
    GoalStatus,
    RuntimeEvent,
    Task,
    new_session_id,
)
from universal_agent.persistence.codec import encode_session_snapshot
from universal_agent.state import (
    EventSourcedSessionStore,
    InMemorySessionStore,
    SessionSnapshot,
    session_from_state,
)
from universal_agent.state.event_store import (
    SESSION_STATE_EVENT,
    EventReplayError,
    InMemoryEventStore,
    rebuild_session_snapshot,
)


def _snapshot() -> SessionSnapshot:
    state = AgentState(
        session_id=new_session_id(),
        goal=Goal("demo goal", ()),
        current_task=Task("root", ()),
    )
    return session_from_state(state)


def _state_event(snapshot: SessionSnapshot) -> RuntimeEvent:
    return RuntimeEvent(
        type=SESSION_STATE_EVENT,
        session_id=snapshot.state.session_id,
        goal_id=snapshot.state.goal.id,
        task_id=snapshot.state.current_task.id,
        data={"snapshot": encode_session_snapshot(snapshot)},
    )


def test_rebuild_returns_most_recent_session_state_event() -> None:
    store = InMemoryEventStore()
    snapshot = _snapshot()
    store.append(_state_event(snapshot))

    snapshot.state.goal.status = GoalStatus.COMPLETED
    store.append(_state_event(snapshot))

    rebuilt = rebuild_session_snapshot(store, snapshot.state.session_id)
    assert rebuilt.state.session_id == snapshot.state.session_id
    assert rebuilt.state.goal.status is GoalStatus.COMPLETED


def test_rebuild_raises_when_no_session_state_events() -> None:
    with pytest.raises(EventReplayError):
        rebuild_session_snapshot(InMemoryEventStore(), new_session_id())


def test_event_sourced_store_loads_from_snapshot_store_when_present() -> None:
    snapshot = _snapshot()
    inner = InMemorySessionStore()
    wrapped = EventSourcedSessionStore(inner, InMemoryEventStore())

    asyncio.run(wrapped.create_session(snapshot))
    loaded = asyncio.run(wrapped.load_session(snapshot.state.session_id))
    assert loaded.state.session_id == snapshot.state.session_id


def test_event_sourced_store_rebuilds_from_events_on_miss() -> None:
    snapshot = _snapshot()
    event_store = InMemoryEventStore()
    event_store.append(_state_event(snapshot))

    fresh = InMemorySessionStore()
    wrapped = EventSourcedSessionStore(fresh, event_store)

    rebuilt = asyncio.run(wrapped.load_session(snapshot.state.session_id))
    assert rebuilt.state.session_id == snapshot.state.session_id
    # The rebuilt snapshot is re-persisted so a second load hits the store.
    again = asyncio.run(wrapped.load_session(snapshot.state.session_id))
    assert again.state.session_id == snapshot.state.session_id


def test_event_sourced_store_raises_when_both_empty() -> None:

    with pytest.raises(EventReplayError):
        asyncio.run(
            EventSourcedSessionStore(InMemorySessionStore(), InMemoryEventStore()).load_session(
                new_session_id()
            )
        )
