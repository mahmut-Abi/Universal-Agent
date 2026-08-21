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
    def __init__(self) -> None:
        self.scaled = False
        self.mutation_calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        if capability == "inspect_pod":
            return immutable_json(
                {
                    "resource": "pod/example-123",
                    "root_cause": "under_replicated",
                    "capability": capability,
                }
            )
        if self.scaled:
            return immutable_json(
                {
                    "healthy": True,
                    "verification_observed": True,
                    "resource": f"deployment/{arguments['name']}",
                    "capability": capability,
                }
            )
        return immutable_json(
            {
                "healthy": False,
                "desired_replicas": 3,
                "ready_replicas": 1,
                "resource": f"deployment/{arguments['name']}",
                "capability": capability,
            }
        )

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.scaled = True
        self.mutation_calls += 1
        return immutable_json(
            {
                "resource": f"deployment/{arguments['name']}",
                "mutation_applied": True,
                "capability": capability,
            }
        )


def execute(capability: str, *observations: str) -> Decision:
    arguments: dict[str, str | int] = {"name": "example"}
    target = "deployment/example"
    if capability == "inspect_pod":
        arguments["name"] = "example-123"
        target = "pod/example-123"
    if capability == "scale_workload":
        arguments.update({"namespace": "default", "replicas": 3})
    return Decision(
        DecisionType.EXECUTE,
        f"Run {capability} behind agentd routes",
        capability=capability,
        target=target,
        arguments=immutable_json(arguments),
        expected_observations=observations,
    )


def build_app(
    decisions: list[Decision],
    *,
    environment: str,
) -> tuple[AgentdApp, RuntimeService, FakeAgentdBackend]:
    backend = FakeAgentdBackend()
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
        environment=immutable_json({"environment": environment}),
    )
    service = RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )
    return AgentdApp(service), service, backend


async def main() -> None:
    app, service, backend = build_app(
        [
            execute("inspect_workload", "healthy"),
            execute("inspect_pod", "root_cause"),
            execute("scale_workload", "mutation_applied"),
            execute("inspect_workload", "verification_observed", "healthy"),
            Decision(DecisionType.FINISH, "Health evaluation completed after resume"),
        ],
        environment="production",
    )
    health = await app.handle(HttpRequest("GET", "/health"))
    ready = await app.handle(HttpRequest("GET", "/ready"))
    capabilities = await app.handle(HttpRequest("GET", "/v1/capabilities"))
    run = await service.run_goal(
        Goal("Restore workload behind agentd routes", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ()),
    )
    waiting_session = await app.handle(HttpRequest("GET", f"/v1/sessions/{run.result.session_id}"))
    resumed = await app.handle(
        HttpRequest(
            "POST",
            f"/v1/sessions/{run.result.session_id}/resume",
            immutable_json({"confirmed": True}),
        )
    )
    assert isinstance(resumed.body["session"], dict)
    session = await app.handle(HttpRequest("GET", f"/v1/sessions/{run.result.session_id}"))
    route_events = await app.handle(
        HttpRequest("GET", f"/v1/sessions/{run.result.session_id}/events")
    )
    capability_items = capabilities.body["capabilities"]
    event_items = route_events.body["events"]
    result_body = resumed.body["result"]
    assert isinstance(capability_items, list)
    assert isinstance(event_items, list)
    assert isinstance(result_body, dict)

    cancel_app, cancel_service, _ = build_app(
        [
            Decision(
                DecisionType.ASK_USER,
                "Operator input is required before continuing",
                message="Which namespace should be inspected?",
            )
        ],
        environment="staging",
    )
    cancel_run = await cancel_service.run_goal(
        Goal("Pause route demo", (SuccessCriterion("healthy", True),)),
        Task("Wait for operator input", ()),
    )
    cancelled = await cancel_app.handle(
        HttpRequest(
            "POST",
            f"/v1/sessions/{cancel_run.result.session_id}/cancel",
            immutable_json({"reason": "operator cancelled route demo"}),
        )
    )
    cancel_result = cancelled.body["result"]
    assert isinstance(cancel_result, dict)

    print(f"health={health.status_code}:{health.body['status']}")
    print(f"ready={ready.body['ready']} reason={ready.body['reason']}")
    print(f"capability_count={len(capability_items)}")
    print(f"initial_status={run.result.status.value}")
    print(f"pending_before_resume={waiting_session.body['pending_action'] is not None}")
    print(f"resumed_status={result_body['status']}")
    print(f"session_status={session.body['goal_status']}")
    print(f"mutation_calls={backend.mutation_calls}")
    print(f"event_count={len(event_items)}")
    print(f"cancel_initial_status={cancel_run.result.status.value}")
    print(f"cancelled_status={cancel_result['status']}")


if __name__ == "__main__":
    asyncio.run(main())
