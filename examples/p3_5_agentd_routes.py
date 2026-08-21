from __future__ import annotations

import asyncio

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DomainLoader,
    InMemoryEventSink,
    InMemoryStateStore,
    RuntimeAPI,
    RuntimeBuilder,
    RuntimeService,
    ScriptedModelAdapter,
    immutable_json,
)
from universal_agent.agentd import AgentdApp, HttpRequest
from universal_agent.core import JsonMapping, JsonValue
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


def session_request_body(
    *,
    goal_description: str,
    task_description: str,
    required_criteria: tuple[str, ...] = (),
    profile: str | None = None,
) -> JsonMapping:
    success_criterion: dict[str, JsonValue] = {"key": "healthy", "expected": True}
    criteria: list[JsonValue] = [success_criterion]
    required: list[JsonValue] = list(required_criteria)
    goal_payload: dict[str, JsonValue] = {
        "description": goal_description,
        "success_criteria": criteria,
    }
    task_payload: dict[str, JsonValue] = {
        "description": task_description,
        "required_criteria": required,
    }
    body: dict[str, JsonValue] = {"goal": goal_payload, "task": task_payload}
    if profile is not None:
        body["profile"] = profile
    return immutable_json(body)


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
    app, _, backend = build_app(
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
    created = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions",
            session_request_body(
                goal_description="Restore workload behind agentd routes",
                task_description="Inspect workload",
            ),
        )
    )
    created_result = created.body["result"]
    assert isinstance(created_result, dict)
    session_id = created_result["session_id"]
    assert isinstance(session_id, str)
    waiting_session = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}"))
    resumed = await app.handle(
        HttpRequest(
            "POST",
            f"/v1/sessions/{session_id}/resume",
            immutable_json({"confirmed": True}),
        )
    )
    assert isinstance(resumed.body["session"], dict)
    session = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}"))
    route_events = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/events"))
    capability_items = capabilities.body["capabilities"]
    event_items = route_events.body["events"]
    result_body = resumed.body["result"]
    assert isinstance(capability_items, list)
    assert isinstance(event_items, list)
    assert isinstance(result_body, dict)

    cancel_app, _, _ = build_app(
        [
            Decision(
                DecisionType.ASK_USER,
                "Operator input is required before continuing",
                message="Which namespace should be inspected?",
            )
        ],
        environment="staging",
    )
    cancel_created = await cancel_app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions",
            session_request_body(
                goal_description="Pause route demo",
                task_description="Wait for operator input",
            ),
        )
    )
    cancel_created_result = cancel_created.body["result"]
    assert isinstance(cancel_created_result, dict)
    cancel_session_id = cancel_created_result["session_id"]
    assert isinstance(cancel_session_id, str)
    cancelled = await cancel_app.handle(
        HttpRequest(
            "POST",
            f"/v1/sessions/{cancel_session_id}/cancel",
            immutable_json({"reason": "operator cancelled route demo"}),
        )
    )
    cancel_result = cancelled.body["result"]
    assert isinstance(cancel_result, dict)

    print(f"health={health.status_code}:{health.body['status']}")
    print(f"ready={ready.body['ready']} reason={ready.body['reason']}")
    print(f"capability_count={len(capability_items)}")
    print(f"initial_status={created_result['status']}")
    print(f"pending_before_resume={waiting_session.body['pending_action'] is not None}")
    print(f"resumed_status={result_body['status']}")
    print(f"session_status={session.body['goal_status']}")
    print(f"mutation_calls={backend.mutation_calls}")
    print(f"event_count={len(event_items)}")
    print(f"cancel_initial_status={cancel_created_result['status']}")
    print(f"cancelled_status={cancel_result['status']}")


if __name__ == "__main__":
    asyncio.run(main())
