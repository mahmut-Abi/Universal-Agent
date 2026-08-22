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
    RuntimeAPI,
    RuntimeBuilder,
    RuntimeService,
    ScriptedModelAdapter,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core import ExecutionStatus, JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.evaluation.harness import EvaluationScenario, ScenarioExpectations
from universal_agent.evaluation.replay import DeterministicReplayHarness


class ReplayBackend:
    def __init__(self, *, initial_timeout: bool = False) -> None:
        self.initial_timeout = initial_timeout
        self.inspect_calls: list[str] = []
        self._timeout_used = False

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls.append(capability)
        if self.initial_timeout and not self._timeout_used:
            self._timeout_used = True
            raise TimeoutError("inspection timed out")
        assert capability == "inspect_workload"
        return immutable_json({"resource": "deployment/example", "healthy": True})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Required evidence is present")


def scenario() -> EvaluationScenario:
    return EvaluationScenario(
        "healthy workload replay",
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
        ScenarioExpectations(
            expected_status=ExecutionStatus.COMPLETED,
            expected_criteria=immutable_json({"healthy": True}),
            required_events=("GoalCompleted", "EvaluationCompleted"),
            required_capabilities=("inspect_workload",),
            max_actions=2,
        ),
    )


def build_service(backend: ReplayBackend, decisions: list[Decision]) -> RuntimeService:
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "staging"}),
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )


@pytest.mark.asyncio
async def test_deterministic_replay_passes_matching_behavior_with_new_ids() -> None:
    first_backend = ReplayBackend()
    expected = await DeterministicReplayHarness(
        build_service(first_backend, [inspect_workload(), finish()])
    ).record(scenario())

    second_backend = ReplayBackend()
    replay = await DeterministicReplayHarness(
        build_service(second_backend, [inspect_workload(), finish()])
    ).replay(scenario(), expected)

    assert replay.passed
    assert replay.failed_checks == ()
    assert expected.action_capabilities == ("inspect_workload",)
    assert first_backend.inspect_calls == ["inspect_workload"]
    assert second_backend.inspect_calls == ["inspect_workload"]


@pytest.mark.asyncio
async def test_deterministic_replay_detects_behavior_drift() -> None:
    expected = await DeterministicReplayHarness(
        build_service(ReplayBackend(), [inspect_workload(), finish()])
    ).record(scenario())

    replay = await DeterministicReplayHarness(
        build_service(ReplayBackend(initial_timeout=True), [inspect_workload(), finish()])
    ).replay(scenario(), expected)

    assert not replay.passed
    assert {check.name for check in replay.failed_checks} >= {
        "event_types",
        "action_capabilities",
        "metrics",
    }
    assert replay.actual.metrics.recovery_planned_count == 1
