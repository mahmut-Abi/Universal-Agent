from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path

import pytest

from universal_agent.core import (
    AgentState,
    EventId,
    Goal,
    JsonMapping,
    RuntimeEvent,
    SessionId,
    SuccessCriterion,
    Task,
    TaskId,
)
from universal_agent.persistence import SQLiteEventStore, SQLiteRuntimeStore, SQLiteSessionStore
from universal_agent.persistence.codec import decode_runtime_event
from universal_agent.state import SessionVersionConflictError, session_from_state


def make_state(session_id: str) -> AgentState:
    goal = Goal("verify deployment", (SuccessCriterion("healthy", True),))
    task = Task("probe", ("healthy",), TaskId("task-1"))
    return AgentState(SessionId(session_id), goal, task)


def make_event(session_id: str, event_type: str, event_id: str) -> RuntimeEvent:
    snapshot = session_from_state(make_state(session_id))
    return RuntimeEvent(
        event_type,
        SessionId(session_id),
        snapshot.state.goal.id,
        snapshot.state.current_task.id,
        EventId(event_id),
    )


@pytest.mark.asyncio
async def test_sqlite_event_store_emit_runs_off_event_loop_thread(tmp_path: Path) -> None:
    """Blocking SQLite work must not run on the event loop thread."""
    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    loop_thread = threading.current_thread()
    observed_threads: list[threading.Thread] = []

    original_append = store.append

    def traced_append(event: RuntimeEvent) -> None:
        observed_threads.append(threading.current_thread())
        original_append(event)

    store.append = traced_append  # type: ignore[method-assign]

    await store.emit(make_event("session-1", "StateUpdated", "event-1"))

    assert [event.id for event in store.events_for(SessionId("session-1"))] == [EventId("event-1")]
    assert observed_threads, "append never executed"
    assert all(thread is not loop_thread for thread in observed_threads)


@pytest.mark.asyncio
async def test_sqlite_event_store_read_runs_off_event_loop_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import universal_agent.persistence.sqlite as sqlite_module

    store = SQLiteEventStore(tmp_path / "events.sqlite3")
    for index in range(3):
        await store.emit(make_event("session-1", "StateUpdated", f"event-{index}"))

    loop_thread = threading.current_thread()
    observed_threads: list[threading.Thread] = []

    original_decode = decode_runtime_event

    def traced_decode(payload: JsonMapping) -> RuntimeEvent:
        observed_threads.append(threading.current_thread())
        return original_decode(payload)

    # Trace the decode step of the read path: after SQL pushdown list_events
    # decodes rows inside the offloaded sync helper, so this seam proves the
    # blocking read work does not run on the event loop thread.
    monkeypatch.setattr(sqlite_module, "decode_runtime_event", traced_decode)

    events = await store.list_events(SessionId("session-1"))

    assert [event.id for event in events] == [EventId(f"event-{index}") for index in range(3)]
    assert observed_threads, "store read never decoded events"
    assert all(thread is not loop_thread for thread in observed_threads)


@pytest.mark.asyncio
async def test_sqlite_session_store_save_keeps_loop_responsive_under_db_lock(
    tmp_path: Path,
) -> None:
    """The event loop must stay responsive while a save blocks on a DB lock.

    A helper thread parks holding an EXCLUSIVE transaction before the save
    starts. After the save task is launched, the test asserts the save has not
    completed and that loop-driven work made progress while the lock was held.
    A synchronous-in-async implementation would freeze the loop inside the
    database call and complete the save before the loop could progress.
    """
    path = tmp_path / "runtime.sqlite3"
    store = SQLiteSessionStore(path)
    snapshot = session_from_state(make_state("session-1"))
    await store.create_session(snapshot)
    # Warm the engine so engine creation (also blocking) is not under test.
    await store.list_sessions()

    loaded = await store.load_session(snapshot.state.session_id)
    loaded.state.iteration = 1

    parked = threading.Event()
    release = threading.Event()

    def park_exclusive() -> None:
        connection = sqlite3.connect(path, timeout=0.1)
        try:
            connection.execute("BEGIN EXCLUSIVE")
            parked.set()
            release.wait(timeout=10)
        finally:
            connection.rollback()
            connection.close()

    parker = threading.Thread(target=park_exclusive, daemon=True)
    parker.start()
    try:
        await asyncio.to_thread(parked.wait, 10)

        save_task = asyncio.create_task(store.save_session(loaded))
        ticks: list[int] = []

        async def heartbeat() -> None:
            for tick in range(10):
                ticks.append(tick)
                await asyncio.sleep(0.001)

        await asyncio.wait_for(heartbeat(), timeout=5)

        assert not save_task.done(), (
            "save completed while the database lock was still held, "
            "meaning it blocked the event loop"
        )
        assert len(ticks) == 10
    finally:
        release.set()
        await asyncio.to_thread(parker.join, 10)

    await asyncio.wait_for(save_task, timeout=5)

    latest = await store.load_session(snapshot.state.session_id)
    assert latest.version == 1
    assert latest.state.iteration == 1


@pytest.mark.asyncio
async def test_sqlite_session_store_supports_concurrent_saves_across_threads(
    tmp_path: Path,
) -> None:
    """Pooled connections must be usable from multiple worker threads."""
    store = SQLiteSessionStore(tmp_path / "runtime.sqlite3")
    await store.create_session(session_from_state(make_state("session-1")))
    await store.create_session(session_from_state(make_state("session-2")))

    first = await store.load_session(SessionId("session-1"))
    second = await store.load_session(SessionId("session-2"))
    first.state.iteration = 1
    second.state.iteration = 2

    await asyncio.gather(store.save_session(first), store.save_session(second))

    assert (await store.load_session(SessionId("session-1"))).version == 1
    assert (await store.load_session(SessionId("session-2"))).version == 1


@pytest.mark.asyncio
async def test_sqlite_session_store_preserves_version_semantics_off_loop(
    tmp_path: Path,
) -> None:
    store = SQLiteSessionStore(tmp_path / "runtime.sqlite3")
    snapshot = session_from_state(make_state("session-1"))
    await store.create_session(snapshot)

    first = await store.load_session(snapshot.state.session_id)
    second = await store.load_session(snapshot.state.session_id)
    first.state.iteration = 1
    await store.save_session(first)

    with pytest.raises(SessionVersionConflictError, match="session version conflict"):
        await store.save_session(second)

    latest = await store.load_session(snapshot.state.session_id)
    assert latest.version == 1

    with pytest.raises(ValueError, match="already exists"):
        await store.create_session(session_from_state(make_state("session-1")))


@pytest.mark.asyncio
async def test_sqlite_runtime_store_commit_session_event_offloads_write(
    tmp_path: Path,
) -> None:
    store = SQLiteRuntimeStore(tmp_path / "runtime.sqlite3")
    snapshot = session_from_state(make_state("session-1"))
    await store.create_session(snapshot)

    first = await store.load_session(snapshot.state.session_id)
    first.state.iteration = 1
    event = RuntimeEvent(
        "StateUpdated",
        first.state.session_id,
        first.state.goal.id,
        first.state.current_task.id,
        id=EventId("event-1"),
    )

    await store.commit_session_event(first, event)

    latest = await store.load_session(snapshot.state.session_id)
    assert latest.version == 1
    assert latest.state.iteration == 1
    assert [item.event_id for item in store.pending_outbox_events()] == [EventId("event-1")]
