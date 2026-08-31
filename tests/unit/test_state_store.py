from __future__ import annotations

from datetime import UTC, datetime

import pytest

from universal_agent.core import (
    ActionId,
    AgentState,
    DomainIdentity,
    Goal,
    ObservationId,
    SessionId,
    SuccessCriterion,
    Task,
    TaskId,
    TaskStatus,
)
from universal_agent.evidence import Evidence
from universal_agent.state import (
    InMemorySessionStore,
    SessionVersionConflictError,
    StateNotFoundError,
    copy_session,
    session_from_state,
)
from universal_agent.state.session import SessionSnapshot, with_state


def make_state(
    session_id: str,
    *,
    created_at: datetime = datetime(2026, 1, 1, tzinfo=UTC),
) -> AgentState:
    goal = Goal("verify deployment", (SuccessCriterion("healthy", True),), created_at=created_at)
    task = Task("probe", ("healthy",), TaskId("task-1"))
    return AgentState(SessionId(session_id), goal, task)


@pytest.mark.asyncio
async def test_create_session_resets_version_to_zero_and_copies() -> None:
    store = InMemorySessionStore()
    snapshot = session_from_state(make_state("session-1"))
    snapshot.version = 5
    await store.create_session(snapshot)

    loaded = await store.load_session(SessionId("session-1"))
    assert loaded.version == 0
    assert snapshot.version == 0


@pytest.mark.asyncio
async def test_create_session_rejects_duplicate_session() -> None:
    store = InMemorySessionStore()
    await store.create_session(session_from_state(make_state("session-1")))
    with pytest.raises(ValueError, match="already exists"):
        await store.create_session(session_from_state(make_state("session-1")))


@pytest.mark.asyncio
async def test_load_session_missing_raises_state_not_found_and_is_lookup_error() -> None:
    store = InMemorySessionStore()
    with pytest.raises(StateNotFoundError):
        await store.load_session(SessionId("missing"))
    with pytest.raises(LookupError):
        await store.load_session(SessionId("missing"))


@pytest.mark.asyncio
async def test_load_session_returns_copy_isolated_from_store() -> None:
    store = InMemorySessionStore()
    await store.create_session(session_from_state(make_state("session-1")))
    loaded = await store.load_session(SessionId("session-1"))
    loaded.state.current_task.status = TaskStatus.COMPLETED

    fresh = await store.load_session(SessionId("session-1"))
    assert fresh.state.current_task.status is TaskStatus.PENDING


@pytest.mark.asyncio
async def test_save_session_version_conflict_raises_and_is_runtime_error() -> None:
    store = InMemorySessionStore()
    await store.create_session(session_from_state(make_state("session-1")))
    first = await store.load_session(SessionId("session-1"))
    second = await store.load_session(SessionId("session-1"))
    await store.save_session(first)

    with pytest.raises(SessionVersionConflictError):
        await store.save_session(second)
    with pytest.raises(RuntimeError):
        await store.save_session(second)


@pytest.mark.asyncio
async def test_save_session_missing_session_raises_state_not_found() -> None:
    store = InMemorySessionStore()
    snapshot = session_from_state(make_state("session-1"))
    with pytest.raises(StateNotFoundError):
        await store.save_session(snapshot)


@pytest.mark.asyncio
async def test_save_session_increments_version_and_persists() -> None:
    store = InMemorySessionStore()
    await store.create_session(session_from_state(make_state("session-1")))
    loaded = await store.load_session(SessionId("session-1"))
    loaded.state.current_task.status = TaskStatus.COMPLETED
    await store.save_session(loaded)

    reloaded = await store.load_session(SessionId("session-1"))
    assert reloaded.version == 1
    assert reloaded.state.current_task.status is TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_list_sessions_sorted_newest_created_at_first() -> None:
    store = InMemorySessionStore()
    older = make_state("session-old", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    newer = make_state("session-new", created_at=datetime(2026, 2, 1, tzinfo=UTC))
    await store.create_session(session_from_state(newer))
    await store.create_session(session_from_state(older))
    sessions = await store.list_sessions()
    assert [str(s.state.session_id) for s in sessions] == ["session-new", "session-old"]


@pytest.mark.asyncio
async def test_create_load_save_state_round_trip_through_state_interface() -> None:
    store = InMemorySessionStore()
    state = make_state("session-1")
    await store.create(state)
    loaded = await store.load(SessionId("session-1"))
    assert loaded.session_id == state.session_id

    loaded.current_task.status = TaskStatus.COMPLETED
    await store.save(loaded)
    reloaded = await store.load(SessionId("session-1"))
    assert reloaded.current_task.status is TaskStatus.COMPLETED


def test_session_from_state_builds_root_graph_and_domains() -> None:
    snapshot = session_from_state(
        make_state("session-1"), domain_name="kubernetes", domain_version="1.0"
    )
    assert snapshot.task_graph.nodes[0].key == "root"
    assert snapshot.domains == (DomainIdentity("kubernetes", "1.0"),)


def test_with_state_attaches_current_task_to_graph() -> None:
    state = make_state("session-1")
    base = session_from_state(state)
    new_task = Task("verify", ("healthy",), TaskId("task-2"))
    updated_state = AgentState(
        state.session_id,
        state.goal,
        new_task,
        tasks=[new_task],
    )
    updated = with_state(base, updated_state)
    assert updated.state.current_task.id == new_task.id
    assert updated.task_graph.current_task_id == new_task.id
    assert updated.version == base.version


def test_copy_session_is_deep_and_preserves_identity_fields() -> None:
    snapshot = session_from_state(make_state("session-1"))
    snapshot.domain_identities = (DomainIdentity("kubernetes", "1.0"),)
    copied = copy_session(snapshot)

    assert copied is not snapshot
    assert copied.state is not snapshot.state
    assert copied.state.current_task is not snapshot.state.current_task
    assert copied.task_graph is not snapshot.task_graph
    assert copied.version == snapshot.version
    assert copied.domains == snapshot.domains


def test_copy_session_isolation_prevents_cross_mutuation() -> None:
    snapshot = session_from_state(make_state("session-1"))
    copied = copy_session(snapshot)
    copied.state.current_task.status = TaskStatus.COMPLETED
    assert snapshot.state.current_task.status is TaskStatus.PENDING


def test_session_domains_property_resolves_from_name_and_version() -> None:
    with_identities = SessionSnapshot(
        make_state("session-1"),
        session_from_state(make_state("session-1")).task_graph,
        domain_identities=(DomainIdentity("kubernetes", "1.0"),),
    )
    assert with_identities.domains == (DomainIdentity("kubernetes", "1.0"),)

    with_name = SessionSnapshot(
        make_state("session-1"),
        session_from_state(make_state("session-1")).task_graph,
        domain_name="kubernetes",
        domain_version="1.0",
    )
    assert with_name.domains == (DomainIdentity("kubernetes", "1.0"),)

    empty = SessionSnapshot(
        make_state("session-1"), session_from_state(make_state("session-1")).task_graph
    )
    assert empty.domains == ()


def test_session_snapshot_evidence_is_preserved_through_copy() -> None:
    base = session_from_state(make_state("session-1"))
    evidence = Evidence(
        SessionId("session-1"),
        TaskId("task-1"),
        ActionId("action-1"),
        ObservationId("observation-1"),
        "pod/a",
        "ready",
        True,
        "test",
    )
    base.evidence = (evidence,)
    copied = copy_session(base)
    assert copied.evidence == (evidence,)
    assert copied.evidence[0] is not evidence
