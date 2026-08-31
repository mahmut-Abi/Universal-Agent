from __future__ import annotations

import pytest

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DomainLoader,
    Goal,
    InMemoryEventSink,
    InMemoryStateStore,
    RuntimeBuilder,
    ScriptedModelAdapter,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core import ErrorCode, ExecutionStatus, JsonMapping
from universal_agent.domain import RuntimeComponents
from universal_agent.domains.kubernetes import KubernetesBackend, KubernetesDomain


class DiagnosticBackend:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.calls.append(capability)
        if capability == "inspect_workload":
            return immutable_json({"resource": "deployment/example", "healthy": False})
        return immutable_json({"resource": "pod/example-123", "root_cause": "crash_loop"})


class TimeoutThenHealthyBackend:
    def __init__(self) -> None:
        self.calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("simulated timeout")
        return immutable_json({"resource": "deployment/example", "healthy": True})


def decision(capability: str, criterion: str) -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        f"Run {capability}",
        capability=capability,
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=(criterion,),
    )


def build_runtime(
    backend: KubernetesBackend,
    decisions: list[Decision],
) -> tuple[AgentRuntime, InMemoryStateStore, InMemoryEventSink, RuntimeComponents]:
    components = RuntimeBuilder().build(DomainLoader().load(KubernetesDomain(backend)))
    events = InMemoryEventSink()
    store = InMemoryStateStore()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=store,
        components=components,
        event_sink=events,
    )
    return runtime, store, events, components


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_observation_builds_evidence_world_and_dynamic_task() -> None:
    backend = DiagnosticBackend()
    runtime, store, events, components = build_runtime(
        backend,
        [
            decision("inspect_workload", "healthy"),
            decision("inspect_pod", "root_cause"),
            Decision(DecisionType.FINISH, "Root cause identified"),
        ],
    )
    result = await runtime.run(
        Goal("Diagnose workload", (SuccessCriterion("root_cause", "crash_loop"),)),
        Task("Inspect workload", ()),
    )
    state = await store.load(result.session_id)
    world = components.world_model.snapshot(result.session_id)
    event_types = [event.type for event in events.events]

    assert result.status is ExecutionStatus.COMPLETED
    assert backend.calls == ["inspect_workload", "inspect_pod"]
    assert len(state.tasks) == 2
    assert any(t == "GoalCompleted" for t in event_types)
    assert world.value_for("healthy") is False
    assert world.value_for("root_cause") == "crash_loop"
    assert "EvidenceRecorded" in event_types
    assert "WorldModelUpdated" in event_types
    assert "TaskStarted" in event_types


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_timeout_recovery_rechecks_policy_and_succeeds() -> None:
    backend = TimeoutThenHealthyBackend()
    runtime, _, events, _ = build_runtime(
        backend,
        [
            decision("inspect_workload", "healthy"),
            Decision(DecisionType.FINISH, "Health verified"),
        ],
    )
    result = await runtime.run(
        Goal("Verify workload", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )
    event_types = [event.type for event in events.events]

    assert result.status is ExecutionStatus.COMPLETED
    assert backend.calls == 2
    assert event_types.count("PolicyChecked") == 2
    assert "RecoveryPlanned" in event_types
    assert any(t == "GoalCompleted" for t in event_types)


class AlwaysTimeoutBackend:
    def __init__(self) -> None:
        self.calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.calls += 1
        raise TimeoutError("always times out")


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_timeout_recovery_stops_when_budget_is_exhausted() -> None:
    backend = AlwaysTimeoutBackend()
    runtime, _, events, _ = build_runtime(
        backend,
        [decision("inspect_workload", "healthy")],
    )
    result = await runtime.run(
        Goal("Verify workload", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code is ErrorCode.TIMEOUT
    assert backend.calls == 3
    assert [event.type for event in events.events].count("RecoveryPlanned") == 2
    assert "RecoveryExhausted" in [event.type for event in events.events]
