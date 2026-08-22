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
    ModelUsage,
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
    EvaluationQualityGate,
    EvaluationScenario,
    EvaluationScenarioKind,
    EvaluationScenarioSelector,
    EvaluationSuite,
    EvaluationSuiteReport,
    ScenarioExpectations,
    evaluate_quality_gate,
    select_scenarios,
    summarize_suite,
)
from universal_agent.evaluation.recording import record_evaluation_suite


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


def test_evaluation_scenario_selector_filters_by_kind_and_tags() -> None:
    goal, task = goal_task()
    smoke = EvaluationScenario(
        "healthy smoke",
        goal,
        task,
        kind=EvaluationScenarioKind.REGRESSION,
        tags=("smoke", "kubernetes"),
    )
    policy = EvaluationScenario(
        "invalid scale policy",
        goal,
        task,
        kind=EvaluationScenarioKind.POLICY,
        tags=("policy", "kubernetes"),
    )
    recovery = EvaluationScenario(
        "timeout recovery",
        goal,
        task,
        kind=EvaluationScenarioKind.RECOVERY,
        tags=("recovery", "kubernetes", "slow"),
    )

    selected = select_scenarios(
        (smoke, policy, recovery),
        EvaluationScenarioSelector(
            kinds=(EvaluationScenarioKind.POLICY, EvaluationScenarioKind.RECOVERY),
            tags=("kubernetes",),
            exclude_tags=("slow",),
        ),
    )

    assert selected == (policy,)


@pytest.mark.asyncio
async def test_evaluation_harness_runs_selected_named_suite() -> None:
    backend = HarnessBackend()
    service = build_service(backend, [scale_workload(replicas=0)])
    goal, task = goal_task()
    suite = EvaluationSuite(
        "kubernetes behavior contract",
        (
            EvaluationScenario(
                "healthy smoke",
                goal,
                task,
                kind=EvaluationScenarioKind.REGRESSION,
                tags=("smoke", "kubernetes"),
            ),
            EvaluationScenario(
                "invalid scale policy",
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
            ),
        ),
    )

    report = await EvaluationHarness(service).run_suite(
        suite,
        selector=EvaluationScenarioSelector(
            kinds=(EvaluationScenarioKind.POLICY,),
            tags=("kubernetes",),
        ),
    )

    assert report.suite_name == "kubernetes behavior contract"
    assert report.scenario_names == ("invalid scale policy",)
    assert report.passed
    assert report.summary.policy_denial_count == 1
    assert report.summary.action_started_count == 0
    assert backend.mutation_calls == 0

    gate_report = evaluate_quality_gate(
        report,
        EvaluationQualityGate(
            min_pass_rate=1.0,
            max_policy_denial_rate=1.0,
            max_average_actions_per_scenario=0.0,
        ),
    )
    strict_gate_report = evaluate_quality_gate(
        report,
        EvaluationQualityGate(min_pass_rate=1.0, max_policy_denial_rate=0.0),
    )

    assert gate_report.passed
    assert gate_report.suite_name == "kubernetes behavior contract"
    assert not strict_gate_report.passed
    assert strict_gate_report.failed_checks[0].name == "policy_denial_rate"


def test_evaluation_quality_gate_validates_thresholds() -> None:
    with pytest.raises(ValueError, match=r"min_pass_rate must be between 0\.0 and 1\.0"):
        EvaluationQualityGate(min_pass_rate=1.1)

    with pytest.raises(ValueError, match="max_average_actions_per_scenario must be non-negative"):
        EvaluationQualityGate(max_average_actions_per_scenario=-1.0)


@pytest.mark.asyncio
async def test_evaluation_harness_passes_a_normal_scenario() -> None:
    backend = HarnessBackend()
    service = build_service(
        backend,
        [inspect_workload(), finish()],
        usage=[
            ModelUsage(
                "scripted",
                "harness-test",
                input_tokens=75,
                output_tokens=20,
                estimated_cost_micros=11,
            ),
            ModelUsage(
                "scripted",
                "harness-test",
                input_tokens=25,
                output_tokens=5,
                estimated_cost_micros=4,
            ),
        ],
    )
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
            max_model_total_tokens=125,
            max_model_estimated_cost_micros=20,
        ),
    )

    report = await EvaluationHarness(service).run(scenario)

    assert report.passed
    assert report.failed_checks == ()
    assert report.metrics.action_started_count == 1
    assert report.metrics.model_call_count == 2
    assert report.metrics.model_total_token_count == 125
    assert report.metrics.model_estimated_cost_micros == 15
    assert backend.inspect_calls == ["inspect_workload"]
    assert backend.mutation_calls == 0


@pytest.mark.asyncio
async def test_evaluation_harness_summarizes_model_usage_across_scenarios() -> None:
    first = build_service(
        HarnessBackend(),
        [inspect_workload(), finish()],
        usage=[
            ModelUsage("scripted", "suite-test", input_tokens=50, output_tokens=10),
            ModelUsage("scripted", "suite-test", input_tokens=20, output_tokens=5),
        ],
    )
    second = build_service(
        HarnessBackend(),
        [inspect_workload(), finish()],
        usage=[
            ModelUsage(
                "scripted",
                "suite-test",
                input_tokens=70,
                output_tokens=15,
                estimated_cost_micros=8,
            ),
            ModelUsage("scripted", "suite-test", input_tokens=10, output_tokens=5),
        ],
    )
    goal_one, task_one = goal_task()
    goal_two, task_two = goal_task()
    first_report = await EvaluationHarness(first).run(
        EvaluationScenario("first", goal_one, task_one)
    )
    second_report = await EvaluationHarness(second).run(
        EvaluationScenario("second", goal_two, task_two)
    )

    report = summarize_suite((first_report, second_report))

    assert report.model_call_count == 4
    assert report.model_total_token_count == 185
    assert report.model_estimated_cost_micros == 8
    assert report.average_model_tokens_per_scenario == 92.5


@pytest.mark.asyncio
async def test_evaluation_harness_records_stable_suite_report() -> None:
    healthy = build_service(HarnessBackend(), [inspect_workload(), finish()])
    policy = build_service(HarnessBackend(), [scale_workload(replicas=0)])
    goal_one, task_one = goal_task()
    goal_two, task_two = goal_task()

    suite = await EvaluationHarness(healthy).run_many(
        (EvaluationScenario("healthy workload", goal_one, task_one),)
    )
    policy_report = await EvaluationHarness(policy).run(
        EvaluationScenario(
            "invalid scale",
            goal_two,
            task_two,
            ScenarioExpectations(
                expected_status=ExecutionStatus.FAILED,
                expected_error_code=ErrorCode.POLICY_DENIED,
                required_audit_capabilities=("scale_workload",),
                policy_denial_count=1,
            ),
        )
    )
    recording = record_evaluation_suite(
        EvaluationSuiteReport((*suite.reports, policy_report), "nightly behavior suite")
    )

    assert recording.suite_name == "nightly behavior suite"
    assert recording.summary.scenario_count == 2
    assert recording.summary.passed_count == 2
    assert recording.scenarios[0].action_capabilities == ("inspect_workload",)
    assert recording.scenarios[1].audit_capabilities == ("scale_workload",)
    assert all(
        "session-" not in check.message for item in recording.scenarios for check in item.checks
    )


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
