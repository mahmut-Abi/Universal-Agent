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
from universal_agent.core import ExecutionStatus, JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.evaluation.harness import EvaluationScenario, ScenarioExpectations
from universal_agent.evaluation.replay import DeterministicReplayHarness


class FakeReplayBackend:
    def __init__(self, *, initial_timeout: bool = False) -> None:
        self.initial_timeout = initial_timeout
        self._timeout_used = False

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
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
        "Inspect workload for deterministic replay",
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


def build_service(*, initial_timeout: bool = False) -> RuntimeService:
    backend = FakeReplayBackend(initial_timeout=initial_timeout)
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


async def main() -> None:
    recording = await DeterministicReplayHarness(build_service()).record(scenario())
    matching = await DeterministicReplayHarness(build_service()).replay(scenario(), recording)
    drifted = await DeterministicReplayHarness(
        build_service(initial_timeout=True)
    ).replay(scenario(), recording)

    print(f"matching_replay={matching.passed}")
    print(
        "drifted_replay="
        f"{drifted.passed} failed_checks="
        + ",".join(check.name for check in drifted.failed_checks)
    )


if __name__ == "__main__":
    asyncio.run(main())
