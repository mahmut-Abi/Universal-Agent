from __future__ import annotations

import asyncio

from universal_agent import (
    AgentProfile,
    AgentRuntime,
    Decision,
    DecisionType,
    DomainConfig,
    DomainLoader,
    InMemoryEventSink,
    InMemoryStateStore,
    RuntimeAPI,
    RuntimeBuilder,
    RuntimeConfig,
    RuntimeService,
    ScriptedModelAdapter,
    UniversalAgentRuntime,
    immutable_json,
)
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class FakeSDKBackend:
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
        "Inspect workload through the SDK",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def profile() -> AgentProfile:
    domain = DomainConfig("kubernetes", "0.2.0")
    return AgentProfile(
        "production-operator",
        "1.0.0",
        "Production Kubernetes operator",
        domain,
        RuntimeConfig(domain=domain),
        (domain,),
    )


async def main() -> None:
    backend = FakeSDKBackend()
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
        environment=immutable_json({"environment": "sdk-example"}),
    )
    service = RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
        profiles=(profile(),),
    )
    sdk = UniversalAgentRuntime(service, default_profile="production-operator")

    result = await sdk.submit_goal(
        "Verify workload through SDK",
        success_criteria={"healthy": True},
        task="Inspect workload",
    )
    session = await sdk.get_session(result.session_id)
    event_batch = await sdk.stream_events(result.session_id, limit=20)

    print(f"status={result.status} reason={result.reason}")
    print(f"session={result.session_id} goal_status={result.goal_status}")
    print(f"current_task={session.current_task_status.value} iterations={result.iteration}")
    print("events=" + " -> ".join(event.type for event in event_batch.events))


if __name__ == "__main__":
    asyncio.run(main())
