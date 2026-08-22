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
from universal_agent.core import ErrorCode, ExecutionStatus, JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.evaluation.harness import (
    EvaluationHarness,
    EvaluationScenario,
    ScenarioExpectations,
)


class HarnessBackend:
    def __init__(self, *, initial_timeout: bool = False) -> None:
        self.initial_timeout = initial_timeout
        self.inspect_calls: list[str] = []
        self.mutation_calls = 0
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
        self.mutation_calls += 1
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


def scale_workload(*, replicas: int = 3) -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Scale workload",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": replicas}),
        expected_observations=("mutation_applied",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Required evidence is present")


def goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )


def build_service(
    backend: HarnessBackend,
    decisions: list[Decision],
) -> RuntimeService:
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
async def test_evaluation_harness_passes_a_normal_scenario() -> None:
    backend = HarnessBackend()
    service = build_service(backend, [inspect_workload(), finish()])
    goal, task = goal_task()
    scenario = EvaluationScenario(
        "healthy workload inspection",
        goal,
        task,
        ScenarioExpectations(
            expected_status=ExecutionStatus.COMPLETED,
            expected_criteria=immutable_json({"healthy": True}),
            required_events=("GoalCompleted", "EvaluationCompleted"),
            required_capabilities=("inspect_workload",),
            allowed_capabilities=("inspect_workload",),
            max_actions=1,
            max_iterations=3,
        ),
    )

    report = await EvaluationHarness(service).run(scenario)

    assert report.passed
    assert report.failed_checks == ()
    assert report.metrics.action_started_count == 1
    assert backend.inspect_calls == ["inspect_workload"]
    assert backend.mutation_calls == 0


@pytest.mark.asyncio
async def test_evaluation_harness_reports_policy_regression_checks() -> None:
    backend = HarnessBackend()
    service = build_service(backend, [scale_workload(replicas=0)])
    goal, task = goal_task()
    scenario = EvaluationScenario(
        "invalid scale is denied",
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
    )

    report = await EvaluationHarness(service).run(scenario)

    assert report.passed
    assert len(report.audit_records) == 1
    assert report.audit_records[0].status == "denied"
    assert backend.mutation_calls == 0


@pytest.mark.asyncio
async def test_evaluation_harness_reports_recovery_regression_checks() -> None:
    backend = HarnessBackend(initial_timeout=True)
    service = build_service(backend, [inspect_workload(), finish()])
    goal, task = goal_task()
    scenario = EvaluationScenario(
        "inspection timeout recovers",
        goal,
        task,
        ScenarioExpectations(
            expected_status=ExecutionStatus.COMPLETED,
            expected_criteria=immutable_json({"healthy": True}),
            required_events=("RecoveryPlanned", "GoalCompleted"),
            required_capabilities=("inspect_workload",),
            recovery_planned_count=1,
            max_actions=2,
        ),
    )

    report = await EvaluationHarness(service).run(scenario)

    assert report.passed
    assert backend.inspect_calls == ["inspect_workload", "inspect_workload"]
