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


class FakeServiceBackend:
    def __init__(self) -> None:
        self.inspect_calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls += 1
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
        "Inspect workload through the Runtime Service",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


async def main() -> None:
    backend = FakeServiceBackend()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                inspect_workload(),
                Decision(DecisionType.FINISH, "Health evaluation completed"),
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

    health = service.health()
    ready = service.ready()
    domains = service.domains()
    capabilities = service.capabilities()
    tools = service.tools()
    run = await service.run_goal(
        Goal("Verify workload through RuntimeService", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )

    print(f"health={health.status} service={health.service}")
    print(f"ready={ready.ready} reason={ready.reason}")
    print("domains=" + ",".join(f"{domain.name}@{domain.version}" for domain in domains))
    print("capabilities=" + ",".join(capability.name for capability in capabilities))
    print("tools=" + ",".join(tool.name for tool in tools))
    print(f"status={run.result.status.value} inspections={backend.inspect_calls}")


if __name__ == "__main__":
    asyncio.run(main())
