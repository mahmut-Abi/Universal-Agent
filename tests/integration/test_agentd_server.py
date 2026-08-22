from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

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
from universal_agent.agentd import AgentdApp, AgentdHttpServer, AgentdServerConfig
from universal_agent.core import JsonMapping, JsonValue
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class ServerBackend:
    def __init__(self) -> None:
        self.inspect_calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls += 1
        assert capability == "inspect_workload"
        return immutable_json({"resource": "deployment/example", "healthy": True})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload through HTTP server",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Required evidence is present")


def goal_submission_body() -> dict[str, JsonValue]:
    return {
        "goal": {
            "description": "Verify workload health",
            "success_criteria": [{"key": "healthy", "expected": True}],
        },
        "task": {"description": "Inspect workload", "required_criteria": ["healthy"]},
    }


def build_app(decisions: list[Decision]) -> tuple[AgentdApp, ServerBackend]:
    backend = ServerBackend()
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
    service = RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )
    return AgentdApp(service), backend


@contextmanager
def running_server(app: AgentdApp) -> Generator[str, None, None]:
    try:
        server = AgentdHttpServer(app, AgentdServerConfig(port=0))
    except PermissionError as exc:
        pytest.skip(f"local socket bind unavailable: {exc}")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.base_url
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def request_json(
    base_url: str,
    method: str,
    path: str,
    body: dict[str, JsonValue] | str | None = None,
) -> tuple[int, dict[str, JsonValue]]:
    data: bytes | None = None
    headers: dict[str, str] = {}
    if isinstance(body, dict):
        data = json.dumps(body).encode("utf-8")
        headers["content-type"] = "application/json"
    elif isinstance(body, str):
        data = body.encode("utf-8")
        headers["content-type"] = "application/json"
    request = Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert isinstance(payload, dict)
            return response.status, payload
    except HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
        assert isinstance(payload, dict)
        return exc.code, payload


def test_agentd_http_server_serves_health_goal_and_event_routes() -> None:
    app, backend = build_app([inspect_workload(), finish()])

    with running_server(app) as base_url:
        health_status, health = request_json(base_url, "GET", "/health")
        created_status, created = request_json(
            base_url,
            "POST",
            "/v1/sessions",
            goal_submission_body(),
        )

        result = created["result"]
        assert isinstance(result, dict)
        session_id = result["session_id"]
        assert isinstance(session_id, str)
        events_status, events = request_json(base_url, "GET", f"/v1/sessions/{session_id}/events")

    assert health_status == 200
    assert health["status"] == "ok"
    assert created_status == 201
    assert result["status"] == "completed"
    assert events_status == 200
    event_items = events["events"]
    assert isinstance(event_items, list)
    last_event = event_items[-1]
    assert isinstance(last_event, dict)
    assert last_event["type"] == "GoalCompleted"
    assert backend.inspect_calls == 1


def test_agentd_http_server_returns_json_errors_before_runtime_routing() -> None:
    app, _ = build_app([])

    with running_server(app) as base_url:
        bad_body_status, bad_body = request_json(base_url, "POST", "/v1/sessions", "[]")
        wrong_method_status, wrong_method = request_json(base_url, "PUT", "/health")

    assert bad_body_status == 400
    assert bad_body["error"] == {
        "code": "bad_request",
        "message": "request body must be a JSON object",
    }
    assert wrong_method_status == 405
    assert wrong_method["error"] == {
        "code": "method_not_allowed",
        "message": "method is not allowed for this route",
    }
