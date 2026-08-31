from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from starlette.testclient import TestClient

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
from universal_agent.agentd import (
    AgentdApp,
    AgentdAuthPolicy,
    AgentdHttpServer,
    AgentdServerConfig,
    build_agentd_asgi_app,
)
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


def build_app(
    decisions: list[Decision],
    *,
    auth: AgentdAuthPolicy | None = None,
) -> tuple[AgentdApp, ServerBackend]:
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
    return AgentdApp(service, auth=auth), backend


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


def request(
    base_url: str,
    method: str,
    path: str,
    body: dict[str, JsonValue] | str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, str, dict[str, str]]:
    data: bytes | None = None
    headers: dict[str, str] = {}
    if isinstance(body, dict):
        data = json.dumps(body).encode("utf-8")
        headers["content-type"] = "application/json"
    elif isinstance(body, str):
        data = body.encode("utf-8")
        headers["content-type"] = "application/json"
    if extra_headers is not None:
        headers.update(extra_headers)
    request = Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8"), dict(response.headers.items())
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8"), dict(exc.headers.items())


def request_json(
    base_url: str,
    method: str,
    path: str,
    body: dict[str, JsonValue] | str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, JsonValue]]:
    status, text, _ = request(base_url, method, path, body, extra_headers)
    payload = json.loads(text)
    assert isinstance(payload, dict)
    return status, payload


def header_value(headers: dict[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    raise AssertionError(f"missing response header: {name}")


@pytest.mark.contract
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


@pytest.mark.contract
def test_agentd_asgi_app_serves_health_without_socket_server() -> None:
    app, _ = build_app([])

    with TestClient(build_agentd_asgi_app(app)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "universal-agent-runtime"}


@pytest.mark.contract
def test_agentd_asgi_app_enforces_configured_body_limit() -> None:
    app, _ = build_app([])

    with TestClient(build_agentd_asgi_app(app, AgentdServerConfig(max_body_bytes=2))) as client:
        response = client.post("/v1/sessions", json=goal_submission_body())

    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "payload_too_large",
            "message": "request body is too large",
        }
    }


@pytest.mark.contract
def test_agentd_http_server_enforces_optional_bearer_auth() -> None:
    app, _ = build_app([], auth=AgentdAuthPolicy("server-token"))

    with running_server(app) as base_url:
        health_status, health = request_json(base_url, "GET", "/health")
        denied_status, denied, denied_headers = request(
            base_url,
            "GET",
            "/v1/config",
        )
        allowed_status, allowed = request_json(
            base_url,
            "GET",
            "/v1/config",
            extra_headers={"Authorization": "Bearer server-token"},
        )

    denied_body = json.loads(denied)
    assert isinstance(denied_body, dict)
    assert health_status == 200
    assert health["status"] == "ok"
    assert denied_status == 401
    assert denied_body["error"] == {
        "code": "unauthorized",
        "message": "authentication required",
    }
    assert header_value(denied_headers, "www-authenticate") == 'Bearer realm="agentd"'
    assert allowed_status == 200
    assert isinstance(allowed["domains"], list)


@pytest.mark.contract
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


@pytest.mark.contract
def test_agentd_http_server_serves_sse_event_stream_batches() -> None:
    app, _ = build_app([inspect_workload(), finish()])

    with running_server(app) as base_url:
        _, created = request_json(base_url, "POST", "/v1/sessions", goal_submission_body())
        result = created["result"]
        assert isinstance(result, dict)
        session_id = result["session_id"]
        assert isinstance(session_id, str)
        status, text, headers = request(
            base_url,
            "GET",
            f"/v1/sessions/{session_id}/events/stream?limit=1",
        )

    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    assert "event: DomainActivated\n" in text
    assert "data: " in text
    assert ": next_cursor=" in text


@pytest.mark.contract
def test_agentd_http_server_serves_web_console() -> None:
    app, backend = build_app([inspect_workload(), finish()])

    with running_server(app) as base_url:
        _, created = request_json(base_url, "POST", "/v1/sessions", goal_submission_body())
        result = created["result"]
        assert isinstance(result, dict)
        session_id = result["session_id"]
        assert isinstance(session_id, str)
        status, text, headers = request(
            base_url,
            "GET",
            f"/console?session_id={session_id}&event_limit=20",
        )

    assert status == 200
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert "Universal Agent Runtime Console" in text
    assert "Verify workload health" in text
    assert "ActionStarted" in text
    assert backend.inspect_calls == 1
