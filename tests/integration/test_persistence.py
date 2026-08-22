from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DomainLoader,
    FileEventStore,
    FileSessionStore,
    Goal,
    RuntimeAPI,
    RuntimeBuilder,
    ScriptedModelAdapter,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core import EventId, ExecutionStatus, GoalStatus, JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


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


def remediation_goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Restore workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ()),
    )


def build_file_api(
    root: Path,
    backend: PersistentRemediationBackend,
    decisions: list[Decision],
) -> RuntimeAPI:
    session_store = FileSessionStore(root)
    event_store = FileEventStore(root)
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
