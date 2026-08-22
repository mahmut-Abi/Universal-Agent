from __future__ import annotations

import asyncio
from tempfile import TemporaryDirectory

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
    EvaluationQualityGate,
    EvaluationScenario,
    EvaluationSuiteReport,
    ScenarioExpectations,
    evaluate_quality_gate,
)
from universal_agent.evaluation.recording import (
    FileEvaluationReportStore,
    record_evaluation_suite,
)


class FakeReportBackend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "inspect_workload"
        return immutable_json({"resource": "deployment/example", "healthy": True})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload for evaluation report",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def invalid_scale() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Attempt invalid scale for evaluation report",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 0}),
        expected_observations=("mutation_applied",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Required evidence is present")


def build_service(decisions: list[Decision]) -> RuntimeService:
    backend = FakeReportBackend()
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


def workload_goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )


async def main() -> None:
    healthy_goal, healthy_task = workload_goal_task()
    policy_goal, policy_task = workload_goal_task()
    healthy_report = await EvaluationHarness(build_service([inspect_workload(), finish()])).run(
        EvaluationScenario(
            "healthy workload",
            healthy_goal,
            healthy_task,
            ScenarioExpectations(
                expected_status=ExecutionStatus.COMPLETED,
                expected_criteria=immutable_json({"healthy": True}),
                required_events=("GoalCompleted", "EvaluationCompleted"),
                required_capabilities=("inspect_workload",),
                max_actions=1,
            ),
        )
    )
    policy_report = await EvaluationHarness(build_service([invalid_scale()])).run(
        EvaluationScenario(
            "invalid scale policy",
            policy_goal,
            policy_task,
            ScenarioExpectations(
                expected_status=ExecutionStatus.FAILED,
                expected_error_code=ErrorCode.POLICY_DENIED,
                forbidden_events=("ActionStarted",),
                required_audit_capabilities=("scale_workload",),
                policy_denial_count=1,
            ),
        )
    )

    suite_report = EvaluationSuiteReport((healthy_report, policy_report), "local behavior suite")
    gate_report = evaluate_quality_gate(
        suite_report,
        EvaluationQualityGate(min_pass_rate=1.0, max_policy_denial_rate=0.5),
    )
    recording = record_evaluation_suite(suite_report, gate_report=gate_report)
    with TemporaryDirectory() as directory:
        store = FileEvaluationReportStore(directory)
        store.save(recording)
        loaded = store.load("local behavior suite")

    print(
        f"suite={loaded.suite_name} passed={loaded.passed} "
        f"scenarios={loaded.summary.scenario_count}"
    )
    if loaded.gate is not None:
        print(f"quality_gate={loaded.gate.passed} checks={len(loaded.gate.checks)}")
    for scenario in loaded.scenarios:
        print(
            f"{scenario.scenario_name}: passed={scenario.passed} events={len(scenario.event_types)}"
        )


if __name__ == "__main__":
    asyncio.run(main())
