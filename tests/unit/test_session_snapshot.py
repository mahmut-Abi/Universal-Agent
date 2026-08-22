from datetime import UTC, datetime, timedelta

import pytest

from universal_agent.core import (
    AgentState,
    DomainIdentity,
    Goal,
    ObservationStatus,
    SessionId,
    SuccessCriterion,
    Task,
    TaskId,
    TaskStatus,
    immutable_json,
    new_action_id,
    new_observation_id,
    new_session_id,
)
from universal_agent.evidence import Evidence, InMemoryEvidenceStore
from universal_agent.state import InMemorySessionStore, SessionSnapshot, session_from_state
from universal_agent.tasks import TaskGraphSnapshot, TaskManager, TaskNodeSnapshot, TaskSpec
from universal_agent.world import FactWorldUpdater, InMemoryWorldModel

SESSION = SessionId("session-snapshot")


def make_evidence(*, claim: str, value: bool, confidence: float, seconds: int) -> Evidence:
    return Evidence(
        SESSION,
        TaskId("task-root"),
        new_action_id(),
        new_observation_id(),
        "deployment/example",
        claim,
        value,
        "test",
        confidence,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds),
    )


def make_state() -> AgentState:
    goal = Goal("Restore workload", (SuccessCriterion("healthy", True),))
    task = Task("Inspect workload", ("healthy",))
    state = AgentState(session_id=new_session_id(), goal=goal, current_task=task)
    state.tasks.append(task)
    state.satisfied_criteria["healthy"] = False
    state.recovery_attempts["task-root:timeout:rule"] = 1
    return state


async def test_session_store_isolates_saved_state() -> None:
    store = InMemorySessionStore()
    state = make_state()
    await store.create(state)

    state.iteration = 7
    state.satisfied_criteria["healthy"] = True

    stored = await store.load(state.session_id)
    assert stored.iteration == 0
    assert stored.satisfied_criteria == {"healthy": False}

    await store.save(state)
    reloaded = await store.load(state.session_id)
    assert reloaded.iteration == 7
    assert reloaded.satisfied_criteria == {"healthy": True}
    assert reloaded.recovery_attempts == {"task-root:timeout:rule": 1}
    assert reloaded.current_task is not state.current_task


async def test_session_snapshot_round_trip_preserves_graph_and_evidence() -> None:
    store = InMemorySessionStore()
    state = make_state()
    manager = TaskManager(state.current_task)
    created = manager.expand(
        (TaskSpec("diagnose", "Diagnose workload", ("root_cause",), (state.current_task.id,)),)
    )
    state.tasks.extend(created)
    evidence = make_evidence(claim="healthy", value=False, confidence=0.99, seconds=1)

    snapshot = SessionSnapshot(
        state,
        manager.snapshot(),
        (evidence,),
        "kubernetes",
        "0.1.0",
        (
            DomainIdentity("kubernetes", "0.1.0"),
            DomainIdentity("observability", "0.1.0"),
        ),
    )
    await store.create_session(snapshot)
    loaded = await store.load_session(state.session_id)

    assert loaded.domain_name == "kubernetes"
    assert loaded.domain_version == "0.1.0"
    assert loaded.domains == (
        DomainIdentity("kubernetes", "0.1.0"),
        DomainIdentity("observability", "0.1.0"),
    )
    assert tuple(node.key for node in loaded.task_graph.nodes) == ("root", "diagnose")
    assert loaded.task_graph.nodes[1].depends_on == (state.current_task.id,)
    assert loaded.task_graph.current_task_id == state.current_task.id
    assert loaded.evidence[0].id == evidence.id
    assert loaded.evidence[0].value is False

    rebuilt = TaskManager.from_snapshot(loaded.task_graph)
    assert rebuilt.current.id == state.current_task.id
    assert rebuilt.expand((TaskSpec("diagnose", "Diagnose workload", ("root_cause",), ()),)) == ()
    assert len(rebuilt.all()) == 2


def test_task_graph_snapshot_rejects_invalid_structures() -> None:
    root = Task("Inspect", ())
    other = Task("Diagnose", ())

    with pytest.raises(ValueError, match="at least one task"):
        TaskManager.from_snapshot(TaskGraphSnapshot((), root.id))

    duplicate_key = TaskGraphSnapshot(
        (TaskNodeSnapshot("root", root), TaskNodeSnapshot("root", other)),
        root.id,
    )
    with pytest.raises(ValueError, match="duplicate task key"):
        TaskManager.from_snapshot(duplicate_key)

    unknown_dependency = TaskGraphSnapshot(
        (TaskNodeSnapshot("root", root, (other.id,)),),
        root.id,
    )
    with pytest.raises(ValueError, match="unknown dependencies"):
        TaskManager.from_snapshot(unknown_dependency)

    missing_current = TaskGraphSnapshot((TaskNodeSnapshot("root", root),), other.id)
    with pytest.raises(ValueError, match="current task does not exist"):
        TaskManager.from_snapshot(missing_current)

    cycle = TaskGraphSnapshot(
        (
            TaskNodeSnapshot("root", root, (other.id,)),
            TaskNodeSnapshot("diagnose", other, (root.id,)),
        ),
        root.id,
    )
    with pytest.raises(ValueError, match="dependency cycle"):
        TaskManager.from_snapshot(cycle)


def test_task_graph_snapshot_preserves_status_and_progress() -> None:
    root = Task("Inspect", ())
    manager = TaskManager(root)
    manager.expand((TaskSpec("diagnose", "Diagnose", (), (root.id,)),))
    manager.complete_current()
    started = manager.start_next()
    assert started is not None

    rebuilt = TaskManager.from_snapshot(manager.snapshot())
    assert rebuilt.current.id == started.id
    assert rebuilt.current.status is TaskStatus.RUNNING
    assert rebuilt.all()[0].status is TaskStatus.COMPLETED
    assert rebuilt.has_unfinished()


def test_world_rebuild_from_evidence_matches_original_snapshot() -> None:
    updaters = (FactWorldUpdater(),)
    store = InMemoryEvidenceStore()
    older_high = make_evidence(claim="healthy", value=False, confidence=0.99, seconds=1)
    newer_low = make_evidence(claim="healthy", value=True, confidence=0.7, seconds=2)
    other_claim = make_evidence(claim="root_cause", value=True, confidence=0.9, seconds=3)

    original = InMemoryWorldModel()
    for evidence in (older_high, newer_low, other_claim):
        store.add(evidence)
        for updater in updaters:
            updater.apply(original, evidence)
    expected = original.snapshot(SESSION)

    exported = store.export(SESSION)
    assert tuple(item.id for item in exported) == (older_high.id, newer_low.id, other_claim.id)

    restored_store = InMemoryEvidenceStore()
    restored_store.replace(SESSION, exported)
    rebuilt = InMemoryWorldModel()
    rebuilt.rebuild(SESSION, restored_store.export(SESSION), updaters)
    actual = rebuilt.snapshot(SESSION)

    assert actual.facts == expected.facts
    assert actual.facts[0].value is False
    assert actual.facts[0].evidence_ids == (older_high.id, newer_low.id)


def test_world_rebuild_discards_previous_session_facts() -> None:
    updaters = (FactWorldUpdater(),)
    model = InMemoryWorldModel()
    stale = make_evidence(claim="healthy", value=True, confidence=0.9, seconds=1)
    model.apply_fact(stale)

    fresh = make_evidence(claim="root_cause", value=False, confidence=0.9, seconds=2)
    model.rebuild(SESSION, (fresh,), updaters)
    snapshot = model.snapshot(SESSION)

    assert tuple(fact.claim for fact in snapshot.facts) == ("root_cause",)


def test_session_from_state_builds_single_node_graph() -> None:
    state = make_state()
    snapshot = session_from_state(state, domain_name="kubernetes", domain_version="0.1.0")

    assert snapshot.task_graph.nodes == (TaskNodeSnapshot("root", state.current_task, ()),)
    assert snapshot.task_graph.current_task_id == state.current_task.id
    assert snapshot.evidence == ()
    assert snapshot.domain_name == "kubernetes"
    assert snapshot.domains == (DomainIdentity("kubernetes", "0.1.0"),)


def test_observation_status_enum_is_unchanged() -> None:
    assert ObservationStatus.SUCCEEDED.value == "succeeded"
    assert immutable_json({"healthy": True})["healthy"] is True
