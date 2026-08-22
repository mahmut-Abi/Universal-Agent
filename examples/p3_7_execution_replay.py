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
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.evaluation.replay import replay_execution


class FakeExecutionReplayBackend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "inspect_workload"
        return immutable_json({"resource": "deployment/example", "healthy": True})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload for execution replay",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Required evidence is present")


def build_service() -> RuntimeService:
    backend = FakeExecutionReplayBackend()
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
    service = build_service()
    run = await service.run_goal(
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )
    replay = replay_execution(await service.list_events(run.result.session_id))

    print(f"session={replay.session_id} terminal={replay.terminal_status}")
    print(f"events={replay.event_count} decisions={len(replay.decisions)}")
    print(f"actions={len(replay.actions)} observations={len(replay.observation_ids)}")
    print(f"evidence={','.join(replay.evidence_ids)}")


if __name__ == "__main__":
    asyncio.run(main())
