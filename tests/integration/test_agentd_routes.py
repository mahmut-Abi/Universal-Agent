from __future__ import annotations

import pytest

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DomainConfig,
    DomainLoader,
    Goal,
    InMemoryEventSink,
    InMemoryStateStore,
    ModelUsage,
    ProfileConfig,
    RuntimeAPI,
    RuntimeBuilder,
    RuntimeConfig,
    RuntimeLimitsConfig,
    RuntimeService,
    ScriptedModelAdapter,
    StoreConfig,
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


def wait() -> Decision:
    return Decision(DecisionType.WAIT, "Operator pause requested")


def goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )


def goal_submission_body(
    *,
    goal_description: str = "Verify workload health",
    task_description: str = "Inspect workload",
    required_criteria: tuple[str, ...] = ("healthy",),
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


def build_service(
    decisions: list[Decision],
    *,
    usage: list[ModelUsage] | None = None,
) -> tuple[RuntimeService, AgentdBackend]:
    backend = AgentdBackend()
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions, usage=usage or ()),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "staging"}),
    )
    api = RuntimeAPI(runtime=runtime, session_store=store, event_reader=events)
    config = RuntimeConfig(
        environment=immutable_json({"environment": "staging"}),
        store=StoreConfig.memory(),
        limits=RuntimeLimitsConfig(max_iterations=12, max_recovery_steps=4),
        domain=DomainConfig("kubernetes", "0.2.0"),
    )
    return RuntimeService(runtime_api=api, components=components, config=config), backend


def build_profile_service(decisions: list[Decision]) -> tuple[RuntimeService, AgentdBackend]:
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
        environment=immutable_json({"environment": "production"}),
    )
    api = RuntimeAPI(runtime=runtime, session_store=store, event_reader=events)
    profile = ProfileConfig.from_mapping(
        {
            "name": "production-operator",
            "version": "1.0.0",
            "description": "Production Kubernetes operator",
            "domain": {"name": "kubernetes", "version": "0.2.0"},
        }
    ).to_profile()
    return (
        RuntimeService(
            runtime_api=api,
            components=components,
            profiles=(profile,),
        ),
        backend,
    )


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
    policies = await app.handle(HttpRequest("GET", "/v1/policies"))
    evaluators = await app.handle(HttpRequest("GET", "/v1/evaluators"))
    memory = await app.handle(HttpRequest("GET", "/v1/memory"))
    config = await app.handle(HttpRequest("GET", "/v1/config"))

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

    scale_policy = find_named(policies.body["policies"], "kubernetes-scale-safety")
    assert scale_policy["policy_type"] == "KubernetesScalePolicy"
    assert scale_policy["effect"] is None

    evaluator = find_named(evaluators.body["evaluators"], "workload-health")
    assert evaluator["evaluator_type"] == "WorkloadHealthEvaluator"

    memories = memory.body["memories"]
    assert isinstance(memories, list)
    assert {item["subject"] for item in memories if isinstance(item, dict)} >= {
        "kubernetes readiness",
        "unhealthy workload triage",
    }
    assert config.status_code == 200
    assert config.body == {
        "environment": {"environment": "staging"},
        "store": {"backend": "memory", "path": None},
        "limits": {"max_iterations": 12, "max_recovery_steps": 4},
        "domains": [{"name": "kubernetes", "version": "0.2.0", "primary": True}],
    }


@pytest.mark.asyncio
async def test_agentd_profile_route_exposes_profile_catalog() -> None:
    service, _ = build_profile_service([])
    app = AgentdApp(service)

    response = await app.handle(HttpRequest("GET", "/v1/profiles"))

    assert response.status_code == 200
    assert response.body["profiles"] == [
        {
            "name": "production-operator",
            "version": "1.0.0",
            "description": "Production Kubernetes operator",
            "domain_name": "kubernetes",
            "domain_version": "0.2.0",
            "domains": [{"name": "kubernetes", "version": "0.2.0"}],
        }
    ]


@pytest.mark.asyncio
async def test_agentd_profile_show_route_exposes_one_profile() -> None:
    service, _ = build_profile_service([])
    app = AgentdApp(service)

    response = await app.handle(HttpRequest("GET", "/v1/profiles/production-operator"))

    assert response.status_code == 200
    assert response.body == {
        "name": "production-operator",
        "version": "1.0.0",
        "description": "Production Kubernetes operator",
        "domain_name": "kubernetes",
        "domain_version": "0.2.0",
        "domains": [{"name": "kubernetes", "version": "0.2.0"}],
    }


@pytest.mark.asyncio
async def test_agentd_profile_show_route_returns_404_for_unknown_profile() -> None:
    service, _ = build_profile_service([])
    app = AgentdApp(service)

    response = await app.handle(HttpRequest("GET", "/v1/profiles/missing-profile"))

    assert response.status_code == 404
    assert response.body["error"] == {
        "code": "not_found",
        "message": "profile not found: missing-profile",
    }


@pytest.mark.asyncio
async def test_agentd_create_session_route_accepts_configured_profile() -> None:
    service, backend = build_profile_service([inspect_workload(), finish()])
    app = AgentdApp(service)

    created = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions",
            goal_submission_body(profile="production-operator"),
        )
    )

    assert created.status_code == 201
    result = created.body["result"]
    assert isinstance(result, dict)
    assert result["status"] == "completed"
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_agentd_create_session_route_rejects_unknown_profile() -> None:
    service, _ = build_profile_service([])
    app = AgentdApp(service)

    response = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions",
            goal_submission_body(profile="missing-profile"),
        )
    )

    assert response.status_code == 400
    assert response.body["error"] == {
        "code": "bad_request",
        "message": "unknown profile: missing-profile",
    }


@pytest.mark.asyncio
async def test_agentd_create_session_route_runs_goal_and_exposes_session_events() -> None:
    service, backend = build_service([inspect_workload(), finish()])
    app = AgentdApp(service)

    created = await app.handle(HttpRequest("POST", "/v1/sessions", goal_submission_body()))

    assert created.status_code == 201
    result = created.body["result"]
    session = created.body["session"]
    assert isinstance(result, dict)
    assert isinstance(session, dict)
    assert result["status"] == "completed"
    assert session["goal_status"] == "completed"
    session_id = result["session_id"]
    assert isinstance(session_id, str)

    fetched = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}"))
    listed = await app.handle(HttpRequest("GET", "/v1/sessions"))
    events = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/events"))
    diagnostics = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/diagnostics"))
    evidence = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/evidence"))
    world = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/world"))

    assert fetched.status_code == 200
    assert fetched.body["session_id"] == session_id
    assert listed.status_code == 200
    session_items = listed.body["sessions"]
    assert isinstance(session_items, list)
    assert len(session_items) == 1
    listed_session = session_items[0]
    assert isinstance(listed_session, dict)
    assert listed_session["session_id"] == session_id
    assert listed_session["goal_description"] == "Verify workload health"
    assert listed_session["goal_status"] == "completed"
    assert listed_session["current_task_status"] == "completed"
    assert listed_session["pending_action"] is False
    assert listed_session["domain_name"] == "kubernetes"
    assert events.status_code == 200
    assert diagnostics.status_code == 200
    assert evidence.status_code == 200
    assert world.status_code == 200
    assert evidence.body["session_id"] == session_id
    assert world.body["session_id"] == session_id
    evidence_items = diagnostics.body["evidence"]
    world_items = diagnostics.body["world_facts"]
    assert isinstance(evidence_items, list)
    assert isinstance(world_items, list)
    evidence_claims = {item["claim"]: item for item in evidence_items if isinstance(item, dict)}
    world_claims = {item["claim"]: item for item in world_items if isinstance(item, dict)}
    assert evidence_claims["healthy"]["value"] is True
    assert world_claims["healthy"]["value"] is True
    assert evidence.body["evidence"] == evidence_items
    assert world.body["world_facts"] == world_items
    event_items = events.body["events"]
    assert isinstance(event_items, list)
    last_event = event_items[-1]
    assert isinstance(last_event, dict)
    assert last_event["type"] == "GoalCompleted"
    assert events.body["next_cursor"] == last_event["event_id"]
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_agentd_web_console_route_renders_runtime_snapshot() -> None:
    service, backend = build_service([inspect_workload(), finish()])
    app = AgentdApp(service)

    created = await app.handle(HttpRequest("POST", "/v1/sessions", goal_submission_body()))
    result = created.body["result"]
    assert isinstance(result, dict)
    session_id = result["session_id"]
    assert isinstance(session_id, str)

    console = await app.handle(
        HttpRequest("GET", f"/console?session_id={session_id}&event_limit=20")
    )
    missing = await app.handle(HttpRequest("GET", "/console?session_id=missing-session"))
    invalid_limit = await app.handle(HttpRequest("GET", "/console?event_limit=0"))

    assert console.status_code == 200
    assert console.headers["content-type"] == "text/html; charset=utf-8"
    assert console.text_body is not None
    assert "Universal Agent Runtime Console" in console.text_body
    assert "Runtime Console" in console.text_body
    assert "Verify workload health" in console.text_body
    assert "kubernetes@0.2.0" in console.text_body
    assert "Capability Catalog" in console.text_body
    assert "inspect_workload" in console.text_body
    assert "Tool Catalog" in console.text_body
    assert "kubernetes_inspect_workload" in console.text_body
    assert "ActionStarted" in console.text_body
    assert "capability=inspect_workload" in console.text_body
    assert missing.status_code == 404
    assert invalid_limit.status_code == 400
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_agentd_session_list_route_supports_cursor_and_limit() -> None:
    service, _ = build_service(
        [
            inspect_workload(),
            finish(),
            inspect_workload(),
            finish(),
            inspect_workload(),
            finish(),
        ]
    )
    app = AgentdApp(service)

    for index in range(3):
        await app.handle(
            HttpRequest(
                "POST",
                "/v1/sessions",
                goal_submission_body(goal_description=f"Verify workload health {index}"),
            )
        )

    first_page = await app.handle(HttpRequest("GET", "/v1/sessions?limit=2"))
    first_items = first_page.body["sessions"]
    assert isinstance(first_items, list)
    first_cursor = first_page.body["next_cursor"]
    assert isinstance(first_cursor, str)
    second_page = await app.handle(HttpRequest("GET", f"/v1/sessions?after={first_cursor}&limit=2"))
    missing_cursor = await app.handle(HttpRequest("GET", "/v1/sessions?after=missing-session"))

    assert first_page.status_code == 200
    assert len(first_items) == 2
    last_first_item = first_items[-1]
    assert isinstance(last_first_item, dict)
    assert first_cursor == last_first_item["session_id"]
    second_items = second_page.body["sessions"]
    assert isinstance(second_items, list)
    assert len(second_items) == 1
    last_second_item = second_items[-1]
    assert isinstance(last_second_item, dict)
    assert second_page.body["next_cursor"] == last_second_item["session_id"]
    assert missing_cursor.status_code == 400
    error = missing_cursor.body["error"]
    assert isinstance(error, dict)
    assert error["message"] == "session cursor not found: missing-session"


@pytest.mark.asyncio
async def test_agentd_operations_routes_expose_metrics_doctor_and_audit() -> None:
    service, backend = build_service(
        [scale_workload(), inspect_workload(), finish()],
        usage=[
            ModelUsage(
                "scripted",
                "agentd-test",
                input_tokens=90,
                output_tokens=20,
                estimated_cost_micros=25,
            ),
            ModelUsage(
                "scripted",
                "agentd-test",
                input_tokens=60,
                output_tokens=10,
                estimated_cost_micros=10,
            ),
            ModelUsage("scripted", "agentd-test", input_tokens=30, output_tokens=5),
        ],
    )
    app = AgentdApp(service)

    created = await app.handle(HttpRequest("POST", "/v1/sessions", goal_submission_body()))
    result = created.body["result"]
    assert isinstance(result, dict)
    session_id = result["session_id"]
    assert isinstance(session_id, str)

    metrics = await app.handle(HttpRequest("GET", "/v1/metrics"))
    prometheus_metrics = await app.handle(HttpRequest("GET", "/v1/metrics/prometheus"))
    cost = await app.handle(HttpRequest("GET", "/v1/cost"))
    doctor = await app.handle(HttpRequest("GET", "/v1/doctor"))
    audit = await app.handle(HttpRequest("GET", "/v1/audit"))
    session_audit = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/audit"))
    session_cost = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/cost"))
    logs = await app.handle(HttpRequest("GET", "/v1/logs"))
    session_logs = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/logs"))
    traces = await app.handle(HttpRequest("GET", "/v1/traces"))
    session_traces = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/traces"))
    otlp_traces = await app.handle(HttpRequest("GET", "/v1/traces/otlp"))
    session_otlp_traces = await app.handle(
        HttpRequest("GET", f"/v1/sessions/{session_id}/traces/otlp")
    )

    assert metrics.status_code == 200
    assert metrics.body["session_count"] == 1
    assert metrics.body["completed_goal_count"] == 1
    assert metrics.body["action_started_count"] == 2
    assert metrics.body["model_call_count"] == 3
    assert metrics.body["model_total_token_count"] == 215
    assert prometheus_metrics.status_code == 200
    assert prometheus_metrics.headers["content-type"] == (
        "text/plain; version=0.0.4; charset=utf-8"
    )
    assert prometheus_metrics.text_body is not None
    assert "universal_agent_runtime_completed_goals 1\n" in prometheus_metrics.text_body
    assert "universal_agent_runtime_model_total_tokens 215\n" in prometheus_metrics.text_body
    assert cost.status_code == 200
    assert cost.body == session_cost.body
    assert cost.body["model_call_count"] == 3
    assert cost.body["total_tokens"] == 215
    assert cost.body["estimated_cost_micros"] == 35
    by_model = cost.body["by_model"]
    assert isinstance(by_model, list)
    first_cost_item = by_model[0]
    assert isinstance(first_cost_item, dict)
    assert first_cost_item["model"] == "agentd-test"
    assert doctor.status_code == 200
    assert doctor.body["status"] == "ok"
    assert logs.status_code == 200
    assert logs.body == session_logs.body
    log_items = logs.body["logs"]
    assert isinstance(log_items, list)
    last_log_item = log_items[-1]
    assert isinstance(last_log_item, dict)
    assert last_log_item["event_type"] == "GoalCompleted"
    assert traces.status_code == 200
    assert traces.body == session_traces.body
    span_items = traces.body["spans"]
    assert isinstance(span_items, list)
    root_span = span_items[0]
    assert isinstance(root_span, dict)
    assert root_span["name"] == "runtime.session"
    assert root_span["status"] == "ok"
    action_span_items = [
        item
        for item in span_items[1:]
        if isinstance(item, dict) and str(item["name"]).startswith("runtime.action.")
    ]
    action_span_names = [item["name"] for item in action_span_items]
    assert action_span_names == [
        "runtime.action.scale_workload",
        "runtime.action.inspect_workload",
    ]
    phase_span_names = {
        item["name"]
        for item in span_items[1:]
        if isinstance(item, dict) and not str(item["name"]).startswith("runtime.action.")
    }
    assert phase_span_names >= {
        "runtime.decision",
        "runtime.model_usage",
        "runtime.policy",
        "runtime.observation",
        "runtime.evaluation",
    }
    assert all(item["parent_span_id"] == root_span["span_id"] for item in action_span_items)
    assert otlp_traces.status_code == 200
    assert otlp_traces.body == session_otlp_traces.body
    resource_spans = otlp_traces.body["resourceSpans"]
    assert isinstance(resource_spans, list)
    resource_span = resource_spans[0]
    assert isinstance(resource_span, dict)
    scope_spans = resource_span["scopeSpans"]
    assert isinstance(scope_spans, list)
    scope_span = scope_spans[0]
    assert isinstance(scope_span, dict)
    exported_spans = scope_span["spans"]
    assert isinstance(exported_spans, list)
    assert len(exported_spans) > 3
    exported_root = exported_spans[0]
    exported_action = exported_spans[1]
    assert isinstance(exported_root, dict)
    assert isinstance(exported_action, dict)
    assert exported_action["kind"] == "SPAN_KIND_CLIENT"
    assert exported_action["parentSpanId"] == exported_root["spanId"]
    assert audit.status_code == 200
    audit_items = audit.body["audit_records"]
    session_audit_items = session_audit.body["audit_records"]
    assert isinstance(audit_items, list)
    assert isinstance(session_audit_items, list)
    assert audit_items == session_audit_items
    assert len(audit_items) == 1
    record = audit_items[0]
    assert isinstance(record, dict)
    assert record["session_id"] == session_id
    assert record["capability"] == "scale_workload"
    assert record["policy_effect"] == "allow"
    assert record["status"] == "succeeded"
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_agentd_events_route_supports_cursor_and_limit() -> None:
    service, _ = build_service([inspect_workload(), finish()])
    app = AgentdApp(service)
    created = await app.handle(HttpRequest("POST", "/v1/sessions", goal_submission_body()))
    result = created.body["result"]
    assert isinstance(result, dict)
    session_id = result["session_id"]
    assert isinstance(session_id, str)

    all_events = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/events"))
    event_items = all_events.body["events"]
    assert isinstance(event_items, list)
    first_event = event_items[0]
    assert isinstance(first_event, dict)

    streamed = await app.handle(
        HttpRequest(
            "GET", f"/v1/sessions/{session_id}/events?after={first_event['event_id']}&limit=2"
        )
    )

    assert streamed.status_code == 200
    streamed_items = streamed.body["events"]
    assert isinstance(streamed_items, list)
    assert len(streamed_items) == 2
    assert streamed_items == event_items[1:3]
    last_streamed = streamed_items[-1]
    assert isinstance(last_streamed, dict)
    assert streamed.body["next_cursor"] == last_streamed["event_id"]


@pytest.mark.asyncio
async def test_agentd_events_stream_route_projects_cursor_batch_as_sse() -> None:
    service, _ = build_service([inspect_workload(), finish()])
    app = AgentdApp(service)
    created = await app.handle(HttpRequest("POST", "/v1/sessions", goal_submission_body()))
    result = created.body["result"]
    assert isinstance(result, dict)
    session_id = result["session_id"]
    assert isinstance(session_id, str)

    response = await app.handle(
        HttpRequest("GET", f"/v1/sessions/{session_id}/events/stream?limit=2")
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.text_body is not None
    assert response.text_body.count("\n\n") == 3
    assert "event: GoalCreated\n" in response.text_body
    assert "data: " in response.text_body
    assert f": next_cursor={response.body['next_cursor']}\n\n" in response.text_body


@pytest.mark.asyncio
async def test_agentd_pause_and_resume_routes_continue_waiting_session_without_pending_action() -> (
    None
):
    service, backend = build_service([wait(), inspect_workload(), finish()])
    app = AgentdApp(service)
    created = await app.handle(HttpRequest("POST", "/v1/sessions", goal_submission_body()))
    result = created.body["result"]
    assert isinstance(result, dict)
    session_id = result["session_id"]
    assert isinstance(session_id, str)

    paused = await app.handle(
        HttpRequest(
            "POST",
            f"/v1/sessions/{session_id}/pause",
            immutable_json({"reason": "operator paused via route"}),
        )
    )
    resumed = await app.handle(HttpRequest("POST", f"/v1/sessions/{session_id}/resume"))
    events = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/events"))

    paused_result = paused.body["result"]
    resumed_result = resumed.body["result"]
    assert isinstance(paused_result, dict)
    assert isinstance(resumed_result, dict)
    assert paused.status_code == 200
    assert paused_result["status"] == "waiting"
    assert resumed.status_code == 200
    assert resumed_result["status"] == "completed"
    assert backend.inspect_calls == 1
    event_items = events.body["events"]
    assert isinstance(event_items, list)
    event_types = [item["type"] for item in event_items if isinstance(item, dict)]
    assert "SessionPaused" in event_types
    assert "SessionResumed" in event_types


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
    assert events.body["next_cursor"] == last_event["event_id"]
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
async def test_agentd_create_session_route_validates_request_body() -> None:
    service, _ = build_service([])
    app = AgentdApp(service)
    invalid_goal: dict[str, JsonValue] = {
        "description": "",
        "success_criteria": [{"key": "healthy", "expected": True}],
    }
    valid_task: dict[str, JsonValue] = {
        "description": "Inspect workload",
        "required_criteria": ["healthy"],
    }
    empty_criteria_goal: dict[str, JsonValue] = {
        "description": "Verify workload health",
        "success_criteria": [],
    }
    invalid_required_task: dict[str, JsonValue] = {
        "description": "Inspect workload",
        "required_criteria": [1],
    }
    valid_goal: dict[str, JsonValue] = {
        "description": "Verify workload health",
        "success_criteria": [{"key": "healthy", "expected": True}],
    }

    wrong_method = await app.handle(HttpRequest("PUT", "/v1/sessions"))
    missing_goal = await app.handle(HttpRequest("POST", "/v1/sessions"))
    empty_goal_description = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions",
            immutable_json({"goal": invalid_goal, "task": valid_task}),
        )
    )
    empty_success_criteria = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions",
            immutable_json({"goal": empty_criteria_goal, "task": valid_task}),
        )
    )
    invalid_required_criteria = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions",
            immutable_json({"goal": valid_goal, "task": invalid_required_task}),
        )
    )

    assert wrong_method.status_code == 405
    assert wrong_method.headers["allow"] == "GET, POST"
    assert missing_goal.status_code == 400
    assert missing_goal.body["error"] == {
        "code": "bad_request",
        "message": "goal is required",
    }
    assert empty_goal_description.status_code == 400
    assert empty_goal_description.body["error"] == {
        "code": "bad_request",
        "message": "goal.description must not be empty",
    }
    assert empty_success_criteria.status_code == 400
    assert empty_success_criteria.body["error"] == {
        "code": "bad_request",
        "message": "goal.success_criteria must not be empty",
    }
    assert invalid_required_criteria.status_code == 400
    assert invalid_required_criteria.body["error"] == {
        "code": "bad_request",
        "message": "task.required_criteria[0] must be a string",
    }


@pytest.mark.asyncio
async def test_agentd_resume_route_validates_request_body() -> None:
    service, _ = build_service([])
    app = AgentdApp(service)

    wrong_method = await app.handle(HttpRequest("GET", "/v1/sessions/session-1/resume"))
    pause_wrong_method = await app.handle(HttpRequest("GET", "/v1/sessions/session-1/pause"))
    cancel_wrong_method = await app.handle(HttpRequest("GET", "/v1/sessions/session-1/cancel"))
    missing_session_without_confirmed = await app.handle(
        HttpRequest("POST", "/v1/sessions/session-1/resume")
    )
    invalid_confirmed = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions/session-1/resume",
            immutable_json({"confirmed": "true"}),
        )
    )
    invalid_pause_reason = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions/session-1/pause",
            immutable_json({"reason": 42}),
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
    assert pause_wrong_method.status_code == 405
    assert pause_wrong_method.headers["allow"] == "POST"
    assert cancel_wrong_method.status_code == 405
    assert cancel_wrong_method.headers["allow"] == "POST"
    assert missing_session_without_confirmed.status_code == 404
    assert missing_session_without_confirmed.body["error"] == {
        "code": "not_found",
        "message": "session not found: session-1",
    }
    assert invalid_confirmed.status_code == 400
    assert invalid_confirmed.body["error"] == {
        "code": "bad_request",
        "message": "resume confirmed must be a boolean",
    }
    assert invalid_pause_reason.status_code == 400
    assert invalid_pause_reason.body["error"] == {
        "code": "bad_request",
        "message": "pause reason must be a string",
    }
    assert invalid_cancel_reason.status_code == 400
    assert invalid_cancel_reason.body["error"] == {
        "code": "bad_request",
        "message": "cancel reason must be a string",
    }


@pytest.mark.asyncio
async def test_agentd_resume_route_requires_confirmed_for_pending_action() -> None:
    service, _ = build_remediation_service(
        [inspect_workload("healthy"), inspect_pod(), scale_workload()]
    )
    app = AgentdApp(service)
    waiting = await service.run_goal(*remediation_goal_task())

    session = await app.handle(HttpRequest("GET", f"/v1/sessions/{waiting.result.session_id}"))
    response = await app.handle(
        HttpRequest("POST", f"/v1/sessions/{waiting.result.session_id}/resume")
    )

    assert session.status_code == 200
    pending_action = session.body["pending_action"]
    assert isinstance(pending_action, dict)
    assert pending_action["attempt"] == 1
    assert isinstance(pending_action["idempotency_key"], str)
    assert isinstance(pending_action["parameters_hash"], str)
    assert len(pending_action["parameters_hash"]) == 64
    assert response.status_code == 400
    assert response.body["error"] == {
        "code": "bad_request",
        "message": "resume requires boolean confirmed for pending action",
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
    missing_pause_session = await app.handle(
        HttpRequest("POST", "/v1/sessions/session-missing/pause")
    )
    missing_events_session = await app.handle(
        HttpRequest("GET", "/v1/sessions/session-missing/events")
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
    assert missing_pause_session.status_code == 404
    assert missing_pause_session.body["error"] == {
        "code": "not_found",
        "message": "session not found: session-missing",
    }
    assert missing_events_session.status_code == 404
    assert missing_events_session.body["error"] == {
        "code": "not_found",
        "message": "session not found: session-missing",
    }
    assert missing_cancel_session.status_code == 404
    assert missing_cancel_session.body["error"] == {
        "code": "not_found",
        "message": "session not found: session-missing",
    }
