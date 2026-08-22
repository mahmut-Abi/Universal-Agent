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
from universal_agent.core import ExecutionStatus, JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.evaluation.harness import (
    EvaluationQualityGate,
    EvaluationScenario,
    EvaluationScenarioKind,
    EvaluationSuite,
    ScenarioExpectations,
)
from universal_agent.evaluation.recording import FileEvaluationReportStore
from universal_agent.evaluation.runner import EvaluationRunner


class FakeRunnerBackend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "inspect_workload"
        return immutable_json({"resource": "deployment/example", "healthy": True})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload through evaluation runner",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Required evidence is present")


def build_service() -> RuntimeService:
    backend = FakeRunnerBackend()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter([inspect_workload(), finish()]),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "staging"}),
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )


def suite() -> EvaluationSuite:
    goal = Goal("Verify workload health", (SuccessCriterion("healthy", True),))
    task = Task("Inspect workload", ("healthy",))
    return EvaluationSuite(
        "runner behavior suite",
        (
            EvaluationScenario(
                "healthy workload",
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
            ),
        ),
    )


async def main() -> None:
    with TemporaryDirectory() as directory:
        result = await EvaluationRunner(
            build_service(),
            report_store=FileEvaluationReportStore(directory),
        ).run_suite(
            suite(),
            gate=EvaluationQualityGate(
                min_pass_rate=1.0,
                max_average_actions_per_scenario=1.0,
            ),
        )
        stored = FileEvaluationReportStore(directory).load("runner behavior suite")

    print(f"suite={result.suite_report.suite_name} passed={result.passed}")
    print(f"gate={result.gate_report.passed} checks={len(result.gate_report.checks)}")
    print(f"stored_report={stored.suite_name} scenarios={stored.summary.scenario_count}")


if __name__ == "__main__":
    asyncio.run(main())
