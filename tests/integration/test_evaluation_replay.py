from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DomainLoader,
    Goal,
    InMemoryEventSink,
    InMemoryStateStore,
    ModelUsage,
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
from universal_agent.evaluation.recording import FileReplayRecordingStore
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


def build_service(
    backend: ReplayBackend,
    decisions: list[Decision],
    *,
    usage: list[ModelUsage] | None = None,
) -> RuntimeService:
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions, usage=usage or ()),
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


@pytest.mark.asyncio
async def test_deterministic_replay_detects_model_usage_drift() -> None:
    expected = await DeterministicReplayHarness(
        build_service(
            ReplayBackend(),
            [inspect_workload(), finish()],
            usage=[
                ModelUsage("scripted", "replay-test", input_tokens=50, output_tokens=10),
                ModelUsage(
                    "scripted",
                    "replay-test",
                    input_tokens=20,
                    output_tokens=5,
                    estimated_cost_micros=6,
                ),
            ],
        )
    ).record(scenario())

    replay = await DeterministicReplayHarness(
        build_service(
            ReplayBackend(),
            [inspect_workload(), finish()],
            usage=[
                ModelUsage("scripted", "replay-test", input_tokens=50, output_tokens=10),
                ModelUsage(
                    "scripted",
                    "replay-test",
                    input_tokens=25,
                    output_tokens=5,
                    estimated_cost_micros=8,
                ),
            ],
        )
    ).replay(scenario(), expected)

    assert not replay.passed
    assert [check.name for check in replay.failed_checks] == ["metrics"]
    assert expected.metrics.model_total_token_count == 85
    assert replay.actual.metrics.model_total_token_count == 90
    assert replay.actual.metrics.model_estimated_cost_micros == 8


@pytest.mark.asyncio
async def test_deterministic_replay_uses_persisted_golden_recording(tmp_path: Path) -> None:
    store = FileReplayRecordingStore(tmp_path)
    recording = await DeterministicReplayHarness(
        build_service(ReplayBackend(), [inspect_workload(), finish()])
    ).record(scenario())
    store.save(recording)

    replay = await DeterministicReplayHarness(
        build_service(ReplayBackend(), [inspect_workload(), finish()])
    ).replay(scenario(), store.load("healthy workload replay"))

    assert replay.passed
    assert store.list_recordings()[0].scenario_name == "healthy workload replay"
