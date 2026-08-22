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
    RuntimeAPI,
    RuntimeBuilder,
    RuntimeService,
    ScriptedModelAdapter,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core import ErrorCode, ExecutionStatus, JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.evaluation.harness import (
    EvaluationQualityGate,
    EvaluationScenario,
    EvaluationScenarioKind,
    EvaluationScenarioSelector,
    EvaluationSuite,
    ScenarioExpectations,
)
from universal_agent.evaluation.recording import FileEvaluationReportStore
from universal_agent.evaluation.runner import EvaluationRunner


class RunnerBackend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "inspect_workload"
        return immutable_json({"resource": "deployment/example", "healthy": True})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload for runner evaluation",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def invalid_scale() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Attempt invalid scale for runner evaluation",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 0}),
        expected_observations=("mutation_applied",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Required evidence is present")


def goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )


def build_service(decisions: list[Decision]) -> RuntimeService:
    backend = RunnerBackend()
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


def healthy_scenario(name: str = "healthy workload") -> EvaluationScenario:
    goal, task = goal_task()
    return EvaluationScenario(
        name,
        goal,
        task,
        ScenarioExpectations(
            expected_status=ExecutionStatus.COMPLETED,
            expected_criteria=immutable_json({"healthy": True}),
            required_events=("GoalCompleted", "EvaluationCompleted"),
            required_capabilities=("inspect_workload",),
            max_actions=1,
        ),
        kind=EvaluationScenarioKind.REGRESSION,
        tags=("smoke", "kubernetes"),
    )


def policy_scenario(name: str = "invalid scale policy") -> EvaluationScenario:
    goal, task = goal_task()
    return EvaluationScenario(
        name,
        goal,
        task,
        ScenarioExpectations(
            expected_status=ExecutionStatus.FAILED,
            expected_error_code=ErrorCode.POLICY_DENIED,
            forbidden_events=("ActionStarted",),
            required_audit_capabilities=("scale_workload",),
            policy_denial_count=1,
            max_actions=0,
        ),
        kind=EvaluationScenarioKind.POLICY,
        tags=("policy", "kubernetes"),
    )


@pytest.mark.asyncio
async def test_evaluation_runner_applies_gate_and_persists_report(tmp_path: Path) -> None:
    store = FileEvaluationReportStore(tmp_path)
    suite = EvaluationSuite("runner smoke suite", (healthy_scenario(),))

    result = await EvaluationRunner(
        build_service([inspect_workload(), finish()]),
        report_store=store,
    ).run_suite(
        suite,
        gate=EvaluationQualityGate(min_pass_rate=1.0, max_average_actions_per_scenario=1.0),
    )

    loaded = store.load("runner smoke suite")

    assert result.passed
    assert result.suite_report.passed
    assert result.gate_report.passed
    assert result.recording.summary.action_started_count == 1
    assert loaded.gate is not None
    assert loaded.gate.passed


@pytest.mark.asyncio
async def test_evaluation_runner_keeps_gate_failure_separate_from_scenario_failure() -> None:
    suite = EvaluationSuite("runner strict gate suite", (healthy_scenario(),))

    result = await EvaluationRunner(build_service([inspect_workload(), finish()])).run_suite(
        suite,
        gate=EvaluationQualityGate(min_pass_rate=1.0, max_average_actions_per_scenario=0.0),
    )

    assert not result.passed
    assert result.suite_report.passed
    assert not result.gate_report.passed
    assert [check.name for check in result.gate_report.failed_checks] == [
        "average_actions_per_scenario"
    ]
    assert result.recording.gate is not None
    assert not result.recording.gate.passed


@pytest.mark.asyncio
async def test_evaluation_runner_filters_suite_and_can_skip_persistence(tmp_path: Path) -> None:
    store = FileEvaluationReportStore(tmp_path)
    suite = EvaluationSuite(
        "runner mixed suite",
        (healthy_scenario(), policy_scenario()),
    )

    result = await EvaluationRunner(
        build_service([invalid_scale()]),
        report_store=store,
    ).run_suite(
        suite,
        selector=EvaluationScenarioSelector(kinds=(EvaluationScenarioKind.POLICY,)),
        suite_name="selected policy suite",
        save_recording=False,
    )

    assert result.passed
    assert result.suite_report.suite_name == "selected policy suite"
    assert result.suite_report.scenario_names == ("invalid scale policy",)
    assert result.recording.suite_name == "selected policy suite"
    assert store.list_reports() == ()
