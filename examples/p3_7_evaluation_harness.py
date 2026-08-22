from __future__ import annotations

import asyncio

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


class FakeEvaluationBackend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "inspect_workload"
        return immutable_json({"resource": "deployment/example", "healthy": True})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload for evaluation",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def invalid_scale() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Attempt invalid scale for policy evaluation",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 0}),
        expected_observations=("mutation_applied",),
    )


def build_service(decisions: list[Decision]) -> RuntimeService:
    backend = FakeEvaluationBackend()
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


async def run_healthy_scenario() -> None:
    goal, task = workload_goal_task()
    report = await EvaluationHarness(build_service([inspect_workload(), finish()])).run(
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
        )
    )
    print(
        f"{report.scenario_name}: "
        f"passed={report.passed} actions={report.metrics.action_started_count}"
    )


async def run_policy_scenario() -> None:
    goal, task = workload_goal_task()
    report = await EvaluationHarness(build_service([invalid_scale()])).run(
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
            ),
        )
    )
    print(
        f"{report.scenario_name}: "
        f"passed={report.passed} denials={report.metrics.policy_denial_count}"
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Required evidence is present")


async def main() -> None:
    await run_healthy_scenario()
    await run_policy_scenario()


if __name__ == "__main__":
    asyncio.run(main())
