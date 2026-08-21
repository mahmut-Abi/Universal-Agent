from __future__ import annotations

import pytest

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
from universal_agent.core import ExecutionStatus, JsonMapping, JsonValue
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class AgentdBackend:
    def __init__(self) -> None:
        self.inspect_calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls += 1
        assert capability == "inspect_workload"
        return immutable_json({"resource": "deployment/example", "healthy": True})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


class AgentdRemediationBackend:
    def __init__(self) -> None:
        self.inspect_calls: list[str] = []
        self.mutation_calls = 0
        self.scaled = False

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls.append(capability)
        if capability == "inspect_workload":
            if not self.scaled:
                return immutable_json(
                    {
                        "resource": "deployment/example",
                        "healthy": False,
                        "desired_replicas": 3,
                        "ready_replicas": 1,
                    }
                )
            return immutable_json(
                {
                    "resource": "deployment/example",
                    "healthy": True,
                    "desired_replicas": 3,
                    "ready_replicas": 3,
                    "verification_observed": True,
                }
            )
        if capability == "inspect_pod":
            return immutable_json(
                {
                    "resource": "pod/example-123",
                    "root_cause": "under_replicated",
                }
            )
        raise AssertionError(f"unexpected inspection capability: {capability}")

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        self.mutation_calls += 1
        self.scaled = True
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def inspect_workload(*observations: str) -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=observations or ("healthy",),
    )


def inspect_pod() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect pod",
        capability="inspect_pod",
        target="pod/example-123",
        arguments=immutable_json({"name": "example-123"}),
        expected_observations=("root_cause",),
    )


def scale_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Scale workload",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 3}),
        expected_observations=("mutation_applied",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Required evidence is present")


def goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )


def build_service(decisions: list[Decision]) -> tuple[RuntimeService, AgentdBackend]:
    backend = AgentdBackend()
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "staging"}),
    )
    api = RuntimeAPI(runtime=runtime, session_store=store, event_reader=events)
    return RuntimeService(runtime_api=api, components=components), backend


def remediation_goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Restore workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ()),
    )


def build_remediation_service(
    decisions: list[Decision],
) -> tuple[RuntimeService, AgentdRemediationBackend]:
    backend = AgentdRemediationBackend()
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "production"}),
    )
    api = RuntimeAPI(runtime=runtime, session_store=store, event_reader=events)
    return RuntimeService(runtime_api=api, components=components), backend


def find_named(items: JsonValue, name: str) -> dict[str, JsonValue]:
    assert isinstance(items, list)
    for item in items:
        assert isinstance(item, dict)
        if item["name"] == name:
            return item
    raise AssertionError(f"missing item: {name}")


@pytest.mark.asyncio
async def test_agentd_catalog_routes_expose_runtime_service_views() -> None:
    service, _ = build_service([])
    app = AgentdApp(service)

    health = await app.handle(HttpRequest("GET", "/health"))
    ready = await app.handle(HttpRequest("GET", "/ready"))
    domains = await app.handle(HttpRequest("GET", "/v1/domains"))
    capabilities = await app.handle(HttpRequest("GET", "/v1/capabilities"))
    tools = await app.handle(HttpRequest("GET", "/v1/tools"))

    assert health.status_code == 200
    assert health.body["status"] == "ok"
    assert health.headers["content-type"] == "application/json"
    assert ready.body["ready"] is True
    assert ready.body["capability_count"] == 6
    assert domains.body["domains"] == [
        {
            "name": "kubernetes",
            "version": "0.2.0",
            "description": "Kubernetes inspection with policy-gated workload remediation",
            "primary": True,
            "ontology": ["Cluster", "Node", "Namespace", "Pod", "Deployment", "Service"],
            "capability_names": [
                "inspect_cluster",
                "inspect_workload",
                "inspect_pod",
                "inspect_logs",
                "inspect_events",
                "scale_workload",
            ],
            "evaluator_names": ["workload-health"],
        }
    ]

    scale = find_named(capabilities.body["capabilities"], "scale_workload")
    assert scale["risk"] == "medium"
    assert scale["tool_names"] == ["kubernetes_scale_workload"]

    scale_tool = find_named(tools.body["tools"], "kubernetes_scale_workload")
    assert scale_tool["side_effect"] == "reversible"
    assert scale_tool["required_arguments"] == ["name", "namespace", "replicas"]


@pytest.mark.asyncio
async def test_agentd_session_and_events_routes_are_json_safe() -> None:
    service, backend = build_service([inspect_workload(), finish()])
    app = AgentdApp(service)
    run = await service.run_goal(*goal_task())

    session = await app.handle(HttpRequest("GET", f"/v1/sessions/{run.result.session_id}"))
    events = await app.handle(HttpRequest("GET", f"/v1/sessions/{run.result.session_id}/events"))

    assert run.result.status is ExecutionStatus.COMPLETED
    assert session.status_code == 200
    assert session.body["session_id"] == str(run.result.session_id)
    assert session.body["goal_status"] == "completed"
    assert session.body["current_task_status"] == "completed"
    assert session.body["latest_evaluation"] == {
        "status": "completed",
        "reason": "workload health criteria satisfied",
        "evaluator_name": "workload-health",
        "matched_criteria": {"healthy": True},
        "task_completed": True,
        "goal_completed": True,
    }
    assert events.status_code == 200
    event_items = events.body["events"]
    assert isinstance(event_items, list)
    last_event = event_items[-1]
    assert isinstance(last_event, dict)
    assert last_event["type"] == "GoalCompleted"
    assert isinstance(last_event["occurred_at"], str)
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_agentd_resume_route_confirms_pending_action() -> None:
    service, backend = build_remediation_service(
        [
            inspect_workload("healthy"),
            inspect_pod(),
            scale_workload(),
            inspect_workload("verification_observed", "healthy"),
            finish(),
        ]
    )
    app = AgentdApp(service)
    waiting = await service.run_goal(*remediation_goal_task())

    response = await app.handle(
        HttpRequest(
            "POST",
            f"/v1/sessions/{waiting.result.session_id}/resume",
            immutable_json({"confirmed": True}),
        )
    )

    assert response.status_code == 200
    result = response.body["result"]
    session = response.body["session"]
    assert isinstance(result, dict)
    assert isinstance(session, dict)
    assert result["status"] == "completed"
    assert result["error_code"] is None
    assert session["goal_status"] == "completed"
    assert session["pending_action"] is None
    assert backend.mutation_calls == 1


@pytest.mark.asyncio
async def test_agentd_resume_route_rejects_pending_action() -> None:
    service, backend = build_remediation_service(
        [inspect_workload("healthy"), inspect_pod(), scale_workload()]
    )
    app = AgentdApp(service)
    waiting = await service.run_goal(*remediation_goal_task())

    response = await app.handle(
        HttpRequest(
            "POST",
            f"/v1/sessions/{waiting.result.session_id}/resume",
            immutable_json({"confirmed": False}),
        )
    )

    assert response.status_code == 200
    result = response.body["result"]
    session = response.body["session"]
    assert isinstance(result, dict)
    assert isinstance(session, dict)
    assert result["status"] == "failed"
    assert result["error_code"] == "confirmation_rejected"
    assert session["goal_status"] == "failed"
    assert session["pending_action"] is None
    assert backend.mutation_calls == 0


@pytest.mark.asyncio
async def test_agentd_cancel_route_cancels_pending_action() -> None:
    service, backend = build_remediation_service(
        [inspect_workload("healthy"), inspect_pod(), scale_workload()]
    )
    app = AgentdApp(service)
    waiting = await service.run_goal(*remediation_goal_task())

    response = await app.handle(
        HttpRequest(
            "POST",
            f"/v1/sessions/{waiting.result.session_id}/cancel",
            immutable_json({"reason": "operator cancelled the session"}),
        )
    )

    assert response.status_code == 200
    result = response.body["result"]
    session = response.body["session"]
    assert isinstance(result, dict)
    assert isinstance(session, dict)
    assert result["status"] == "cancelled"
    assert result["error_code"] is None
    assert result["reason"] == "operator cancelled the session"
    assert session["goal_status"] == "cancelled"
    assert session["current_task_status"] == "cancelled"
    assert session["pending_action"] is None
    assert backend.mutation_calls == 0


@pytest.mark.asyncio
async def test_agentd_resume_route_validates_request_body() -> None:
    service, _ = build_service([])
    app = AgentdApp(service)

    wrong_method = await app.handle(HttpRequest("GET", "/v1/sessions/session-1/resume"))
    cancel_wrong_method = await app.handle(HttpRequest("GET", "/v1/sessions/session-1/cancel"))
    missing_confirmed = await app.handle(HttpRequest("POST", "/v1/sessions/session-1/resume"))
    invalid_confirmed = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions/session-1/resume",
            immutable_json({"confirmed": "true"}),
        )
    )
    invalid_cancel_reason = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions/session-1/cancel",
            immutable_json({"reason": 42}),
        )
    )

    assert wrong_method.status_code == 405
    assert wrong_method.headers["allow"] == "POST"
    assert cancel_wrong_method.status_code == 405
    assert cancel_wrong_method.headers["allow"] == "POST"
    assert missing_confirmed.status_code == 400
    assert missing_confirmed.body["error"] == {
        "code": "bad_request",
        "message": "resume requires boolean confirmed",
    }
    assert invalid_confirmed.status_code == 400
    assert invalid_confirmed.body["error"] == {
        "code": "bad_request",
        "message": "resume requires boolean confirmed",
    }
    assert invalid_cancel_reason.status_code == 400
    assert invalid_cancel_reason.body["error"] == {
        "code": "bad_request",
        "message": "cancel reason must be a string",
    }


@pytest.mark.asyncio
async def test_agentd_routes_return_404_and_405_errors() -> None:
    service, _ = build_service([])
    app = AgentdApp(service)

    missing_route = await app.handle(HttpRequest("GET", "/v1/missing"))
    wrong_method = await app.handle(HttpRequest("POST", "/health"))
    missing_session = await app.handle(HttpRequest("GET", "/v1/sessions/session-missing"))
    missing_resume_session = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions/session-missing/resume",
            immutable_json({"confirmed": True}),
        )
    )
    missing_cancel_session = await app.handle(
        HttpRequest("POST", "/v1/sessions/session-missing/cancel")
    )

    assert missing_route.status_code == 404
    assert missing_route.body["error"] == {
        "code": "not_found",
        "message": "unknown route: /v1/missing",
    }
    assert wrong_method.status_code == 405
    assert wrong_method.headers["allow"] == "GET"
    assert wrong_method.body["error"] == {
        "code": "method_not_allowed",
        "message": "method is not allowed for this route",
    }
    assert missing_session.status_code == 404
    assert missing_session.body["error"] == {
        "code": "not_found",
        "message": "session not found: session-missing",
    }
    assert missing_resume_session.status_code == 404
    assert missing_resume_session.body["error"] == {
        "code": "not_found",
        "message": "session not found: session-missing",
    }
    assert missing_cancel_session.status_code == 404
    assert missing_cancel_session.body["error"] == {
        "code": "not_found",
        "message": "session not found: session-missing",
    }
