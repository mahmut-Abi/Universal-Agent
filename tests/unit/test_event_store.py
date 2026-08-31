from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent.core import (
    AgentState,
    EventId,
    Goal,
    GoalStatus,
    RuntimeEvent,
    SessionId,
    SuccessCriterion,
    Task,
    TaskId,
    TaskStatus,
)
from universal_agent.state import SessionSnapshot, copy_session, session_from_state
from universal_agent.state.event_store import (
    EventReplayError,
    FileEventStore,
    InMemoryEventStore,
    rebuild_session,
)


def make_state(session_id: str) -> SessionSnapshot:
    goal = Goal("verify deployment", (SuccessCriterion("healthy", True),))
    task = Task("probe", ("healthy",), TaskId("task-1"))
    state = AgentState(SessionId(session_id), goal, task)
    return session_from_state(
        state,
        domain_name="kubernetes",
        domain_version="1.0.0",
    )


def make_event(session_id: str, event_type: str, event_id: str) -> RuntimeEvent:
    snapshot = make_state(session_id)
    return RuntimeEvent(
        event_type,
        SessionId(session_id),
        snapshot.state.goal.id,
        snapshot.state.current_task.id,
        EventId(event_id),
    )


def reducer(event: RuntimeEvent, snapshot: SessionSnapshot) -> SessionSnapshot:
    if event.type not in {"GoalRunning", "TaskCompleted", "GoalFailed"}:
        raise ValueError(f"unknown event type: {event.type}")
    copied = copy_session(snapshot)
    if event.type == "GoalRunning":
        copied.state.goal.status = GoalStatus.RUNNING
    elif event.type == "TaskCompleted":
        copied.state.current_task.status = TaskStatus.COMPLETED
    elif event.type == "GoalFailed":
        copied.state.goal.status = GoalStatus.FAILED
    return copied


def test_append_and_query_by_session() -> None:
    store = InMemoryEventStore()
    store.append(make_event("s1", "GoalRunning", "e1"))
    store.append(make_event("s2", "GoalRunning", "e2"))
    store.append(make_event("s1", "TaskCompleted", "e3"))

    assert {e.id for e in store.all()} == {EventId("e1"), EventId("e2"), EventId("e3")}
    assert {e.id for e in store.events_for(SessionId("s1"))} == {EventId("e1"), EventId("e3")}
    assert store.events_for(SessionId("s-missing")) == ()


def test_duplicate_event_id_is_idempotent() -> None:
    store = InMemoryEventStore()
    event = make_event("s1", "GoalRunning", "e1")
    store.append(event)
    store.append(event)
    store.append(make_event("s1", "GoalRunning", "e1"))

    assert len(store.events_for(SessionId("s1"))) == 1


def test_rebuild_produces_final_state() -> None:
    store = InMemoryEventStore()
    store.append(make_event("s1", "GoalRunning", "e1"))
    store.append(make_event("s1", "TaskCompleted", "e2"))

    final = rebuild_session(
        store,
        SessionId("s1"),
        initial=make_state("s1"),
        reducer=reducer,
    )

    assert final.state.goal.status is GoalStatus.RUNNING
    assert final.state.current_task.status is TaskStatus.COMPLETED


def test_rebuild_is_repeatable() -> None:
    store = InMemoryEventStore()
    store.append(make_event("s1", "GoalRunning", "e1"))
    store.append(make_event("s1", "GoalFailed", "e2"))

    first = rebuild_session(store, SessionId("s1"), initial=make_state("s1"), reducer=reducer)
    second = rebuild_session(store, SessionId("s1"), initial=make_state("s1"), reducer=reducer)

    assert first.state.goal.status is second.state.goal.status
    assert first.state.current_task.status is second.state.current_task.status


def test_rebuild_unknown_event_raises() -> None:
    store = InMemoryEventStore()
    store.append(make_event("s1", "MysteryEvent", "e1"))

    with pytest.raises(EventReplayError, match="unknown event type"):
        rebuild_session(store, SessionId("s1"), initial=make_state("s1"), reducer=reducer)


def test_file_event_store_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store = FileEventStore(path)
    store.append(make_event("s1", "GoalRunning", "e1"))
    store.append(make_event("s1", "TaskCompleted", "e2"))
    store.append(make_event("s1", "GoalRunning", "e1"))

    assert len(store.events_for(SessionId("s1"))) == 2

    final = rebuild_session(store, SessionId("s1"), initial=make_state("s1"), reducer=reducer)
    assert final.state.goal.status is GoalStatus.RUNNING
    assert final.state.current_task.status is TaskStatus.COMPLETED

    reload = FileEventStore(path)
    assert len(reload.events_for(SessionId("s1"))) == 2
