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


class FakeLogBackend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json(
            {
                "healthy": True,
                "resource": f"deployment/{arguments['name']}",
                "capability": capability,
            }
        )

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json(
            {
                "resource": f"deployment/{arguments['name']}",
                "mutation_applied": True,
                "capability": capability,
            }
        )


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload with structured logs",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


async def main() -> None:
    backend = FakeLogBackend()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                inspect_workload(),
                Decision(DecisionType.FINISH, "Health evidence is present"),
            ]
        ),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "staging"}),
    )
    service = RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )
    run = await service.run_goal(
        Goal("Verify workload with structured logs", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )
    logs = await service.logs(run.result.session_id)

    print(f"status={run.result.status.value} logs={len(logs)}")
    for record in logs[:3]:
        print(f"{record.level}:{record.event_type}:{record.message}")
    print(f"last={logs[-1].event_type}:{logs[-1].message}")


if __name__ == "__main__":
    asyncio.run(main())
