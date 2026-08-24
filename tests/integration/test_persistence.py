from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DomainLoader,
    FileRuntimeStore,
    FileSessionStore,
    Goal,
    RuntimeAPI,
    RuntimeBuilder,
    ScriptedModelAdapter,
    SQLiteEventStore,
    SQLiteRuntimeStore,
    SQLiteSessionStore,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core import (
    AgentState,
    EventId,
    ExecutionStatus,
    GoalStatus,
    JsonMapping,
    RuntimeEvent,
    new_session_id,
)
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.persistence import encode_runtime_event, encode_session_snapshot
from universal_agent.state import SessionVersionConflictError, session_from_state


class PersistentRemediationBackend:
    def __init__(self) -> None:
        self.inspect_calls: list[str] = []
        self.mutation_calls = 0
        self._scaled = False

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls.append(capability)
        if capability == "inspect_workload":
            return immutable_json(
                {
                    "resource": "deployment/example",
                    "healthy": self._scaled,
                    "desired_replicas": 3,
                    "ready_replicas": 3 if self._scaled else 1,
                    "verification_observed": self._scaled,
                }
            )
        if capability == "inspect_pod":
            return immutable_json(
                {
                    "resource": "pod/example-123",
                    "root_cause": "under_replicated",
                }
            )
        raise AssertionError(f"unexpected capability: {capability}")

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        self.mutation_calls += 1
        self._scaled = True
        return immutable_json(
            {
                "resource": "deployment/example",
                "mutation_applied": True,
                "replicas": 3,
            }
        )


def inspect_workload(*expected: str) -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=expected,
    )


def inspect_pod() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect pod",
        capability="inspect_pod",
        target="pod/example-123",
        arguments=immutable_json({"name": "example-123"}),
        expected_observations=("root_cause",),
    )


def scale_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Scale workload",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 3}),
        expected_observations=("mutation_applied",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Required evidence is present")


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["file", "sqlite"])
async def test_persistent_session_stores_reject_stale_snapshot_versions(
    tmp_path: Path,
    store_kind: str,
) -> None:
    store = (
        FileSessionStore(tmp_path / "file-store")
        if store_kind == "file"
        else SQLiteSessionStore(tmp_path / "runtime.sqlite3")
    )
    goal = Goal("Persist version", (SuccessCriterion("healthy", True),))
    task = Task("Inspect", ("healthy",))
    snapshot = session_from_state(goal_state(goal, task))
    await store.create_session(snapshot)

    first = await store.load_session(snapshot.state.session_id)
    second = await store.load_session(snapshot.state.session_id)
    first.state.iteration = 1

    await store.save_session(first)

    assert first.version == 1
    with pytest.raises(SessionVersionConflictError, match="session version conflict"):
        await store.save_session(second)
    latest = await store.load_session(snapshot.state.session_id)
    assert latest.version == 1
    assert latest.state.iteration == 1


@pytest.mark.asyncio
async def test_sqlite_runtime_store_commits_session_and_event_atomically(
    tmp_path: Path,
) -> None:
    store = SQLiteRuntimeStore(tmp_path / "runtime.sqlite3")
    goal = Goal("Atomic state event", (SuccessCriterion("healthy", True),))
    task = Task("Inspect", ("healthy",))
    snapshot = session_from_state(goal_state(goal, task))
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
    events = await store.list_events(snapshot.state.session_id)
    assert first.version == 1
    assert latest.version == 1
    assert latest.state.iteration == 1
    assert [item.id for item in events] == [EventId("event-1")]

    duplicate_event_snapshot = await store.load_session(snapshot.state.session_id)
    duplicate_event_snapshot.state.iteration = 2
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        await store.commit_session_event(duplicate_event_snapshot, event)

    after_duplicate = await store.load_session(snapshot.state.session_id)
    assert duplicate_event_snapshot.version == 1
    assert after_duplicate.version == 1
    assert after_duplicate.state.iteration == 1
    assert [item.id for item in await store.list_events(snapshot.state.session_id)] == [
        EventId("event-1")
    ]


@pytest.mark.asyncio
async def test_file_runtime_store_commits_session_and_event_through_journal(
    tmp_path: Path,
) -> None:
    store = FileRuntimeStore(tmp_path)
    goal = Goal("Journal state event", (SuccessCriterion("healthy", True),))
    task = Task("Inspect", ("healthy",))
    snapshot = session_from_state(goal_state(goal, task))
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
    events = await store.list_events(snapshot.state.session_id)
    assert first.version == 1
    assert latest.version == 1
    assert latest.state.iteration == 1
    assert [item.id for item in events] == [EventId("event-1")]
    assert not list((tmp_path / "commits").glob("*.json"))

    duplicate_event_snapshot = await store.load_session(snapshot.state.session_id)
    duplicate_event_snapshot.state.iteration = 2
    with pytest.raises(ValueError, match="runtime event already exists"):
        await store.commit_session_event(duplicate_event_snapshot, event)

    after_duplicate = await store.load_session(snapshot.state.session_id)
    assert duplicate_event_snapshot.version == 1
    assert after_duplicate.version == 1
    assert after_duplicate.state.iteration == 1
    assert [item.id for item in await store.list_events(snapshot.state.session_id)] == [
        EventId("event-1")
    ]


@pytest.mark.asyncio
async def test_file_runtime_store_recovers_incomplete_journal_commit(
    tmp_path: Path,
) -> None:
    store = FileRuntimeStore(tmp_path)
    goal = Goal("Recover journal state event", (SuccessCriterion("healthy", True),))
    task = Task("Inspect", ("healthy",))
    snapshot = session_from_state(goal_state(goal, task))
    await store.create_session(snapshot)

    pending = await store.load_session(snapshot.state.session_id)
    pending.state.iteration = 1
    pending.version += 1
    event = RuntimeEvent(
        "StateUpdated",
        pending.state.session_id,
        pending.state.goal.id,
        pending.state.current_task.id,
        id=EventId("event-1"),
    )
    commit_path = tmp_path / "commits" / "event-1.json"
    commit_path.parent.mkdir(parents=True)
    with commit_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "session": encode_session_snapshot(pending),
                "event": encode_runtime_event(event),
            },
            handle,
        )

    recovered = FileRuntimeStore(tmp_path)
    latest = await recovered.load_session(snapshot.state.session_id)
    events = await recovered.list_events(snapshot.state.session_id)

    assert latest.version == 1
    assert latest.state.iteration == 1
    assert [item.id for item in events] == [EventId("event-1")]
    assert not list((tmp_path / "commits").glob("*.json"))


def goal_state(goal: Goal, task: Task) -> AgentState:
    state = AgentState(session_id=new_session_id(), goal=goal, current_task=task)
    state.tasks.append(task)
    return state


def remediation_goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Restore workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ()),
    )


def build_sqlite_api(
    path: Path,
    backend: PersistentRemediationBackend,
    decisions: list[Decision],
) -> RuntimeAPI:
    session_store = SQLiteSessionStore(path)
    event_store = SQLiteEventStore(path)
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=session_store,
        components=RuntimeBuilder().build(
            DomainLoader().load(KubernetesRemediationDomain(backend, backend))
        ),
        event_sink=event_store,
        environment=immutable_json({"environment": "production"}),
    )
    return RuntimeAPI(
        runtime=runtime,
        session_store=session_store,
        event_reader=event_store,
    )


def build_file_api(
    root: Path,
    backend: PersistentRemediationBackend,
    decisions: list[Decision],
) -> RuntimeAPI:
    store = FileRuntimeStore(root)
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=store,
        components=RuntimeBuilder().build(
            DomainLoader().load(KubernetesRemediationDomain(backend, backend))
        ),
        event_sink=store,
        environment=immutable_json({"environment": "production"}),
    )
    return RuntimeAPI(
        runtime=runtime,
        session_store=store,
        event_reader=store,
    )


@pytest.mark.asyncio
async def test_file_persistence_resumes_confirmation_after_runtime_rebuild(
    tmp_path: Path,
) -> None:
    backend = PersistentRemediationBackend()
    first = build_file_api(
        tmp_path,
        backend,
        [inspect_workload("healthy"), inspect_pod(), scale_workload()],
    )

    waiting = await first.run_goal(*remediation_goal_task())

    assert waiting.result.status is ExecutionStatus.WAITING
    assert waiting.session.pending_action is not None
    assert backend.mutation_calls == 0
    assert list((tmp_path / "sessions").glob("*.json"))
    assert (tmp_path / "events.jsonl").exists()

    second = build_file_api(
        tmp_path,
        backend,
        [inspect_workload("verification_observed", "healthy"), finish()],
    )
    completed = await second.resume_session(waiting.result.session_id, confirmed=True)
    combined_events = await second.list_events(waiting.result.session_id)

    assert completed.result.status is ExecutionStatus.COMPLETED
    assert completed.session.goal_status is GoalStatus.COMPLETED
    assert completed.session.pending_action is None
    assert backend.mutation_calls == 1
    assert [event.type for event in combined_events][-1] == "GoalCompleted"
    assert [event.type for event in combined_events].count("PolicyChecked") == 5

    third = build_file_api(tmp_path, backend, [])
    reloaded = await third.get_session(waiting.result.session_id)
    reloaded_sessions = await third.list_sessions()
    reloaded_events = await third.list_events(waiting.result.session_id)
    reloaded_batch = await third.stream_events(
        waiting.result.session_id,
        after_event_id=EventId(reloaded_events[0].event_id),
        limit=2,
    )

    assert reloaded.goal_status is GoalStatus.COMPLETED
    assert [item.session_id for item in reloaded_sessions] == [waiting.result.session_id]
    assert reloaded_sessions[0].goal_status is GoalStatus.COMPLETED
    assert reloaded_sessions[0].current_task_status is reloaded.current_task_status
    assert reloaded_sessions[0].pending_action is False
    assert reloaded.latest_evaluation is not None
    assert reloaded.latest_evaluation.goal_completed
    assert tuple(event.type for event in reloaded_events) == tuple(
        event.type for event in combined_events
    )
    assert tuple(event.type for event in reloaded_batch.events) == tuple(
        event.type for event in reloaded_events[1:3]
    )
    assert reloaded_batch.next_cursor == reloaded_batch.events[-1].event_id


@pytest.mark.asyncio
async def test_sqlite_persistence_resumes_confirmation_after_runtime_rebuild(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    backend = PersistentRemediationBackend()
    first = build_sqlite_api(
        db_path,
        backend,
        [inspect_workload("healthy"), inspect_pod(), scale_workload()],
    )

    waiting = await first.run_goal(*remediation_goal_task())

    assert waiting.result.status is ExecutionStatus.WAITING
    assert waiting.session.pending_action is not None
    assert db_path.exists()
    assert backend.mutation_calls == 0

    second = build_sqlite_api(
        db_path,
        backend,
        [inspect_workload("verification_observed", "healthy"), finish()],
    )
    completed = await second.resume_session(waiting.result.session_id, confirmed=True)
    events = await second.list_events(waiting.result.session_id)
    event_batch = await second.stream_events(
        waiting.result.session_id,
        after_event_id=EventId(events[0].event_id),
        limit=3,
    )

    assert completed.result.status is ExecutionStatus.COMPLETED
    assert completed.session.goal_status is GoalStatus.COMPLETED
    assert completed.session.pending_action is None
    assert backend.mutation_calls == 1
    assert [event.type for event in events][-1] == "GoalCompleted"
    assert tuple(event.type for event in event_batch.events) == tuple(
        event.type for event in events[1:4]
    )

    third = build_sqlite_api(db_path, backend, [])
    reloaded = await third.get_session(waiting.result.session_id)
    reloaded_sessions = await third.list_sessions()

    assert reloaded.goal_status is GoalStatus.COMPLETED
    assert [item.session_id for item in reloaded_sessions] == [waiting.result.session_id]
