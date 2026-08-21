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
from universal_agent.agentd import AgentdApp, HttpRequest
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class FakeAgentdBackend:
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
        "Inspect workload behind agentd routes",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


async def main() -> None:
    backend = FakeAgentdBackend()
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
    app = AgentdApp(service)

    health = await app.handle(HttpRequest("GET", "/health"))
    ready = await app.handle(HttpRequest("GET", "/ready"))
    capabilities = await app.handle(HttpRequest("GET", "/v1/capabilities"))
    run = await service.run_goal(
        Goal("Verify workload behind agentd routes", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )
    session = await app.handle(HttpRequest("GET", f"/v1/sessions/{run.result.session_id}"))
    route_events = await app.handle(
        HttpRequest("GET", f"/v1/sessions/{run.result.session_id}/events")
    )
    capability_items = capabilities.body["capabilities"]
    event_items = route_events.body["events"]
    assert isinstance(capability_items, list)
    assert isinstance(event_items, list)

    print(f"health={health.status_code}:{health.body['status']}")
    print(f"ready={ready.body['ready']} reason={ready.body['reason']}")
    print(f"capability_count={len(capability_items)}")
    print(f"session_status={session.body['goal_status']}")
    print(f"event_count={len(event_items)}")


if __name__ == "__main__":
    asyncio.run(main())
