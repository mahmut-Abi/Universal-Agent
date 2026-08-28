from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from io import StringIO
from threading import Thread
from typing import Any, cast

import pytest

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
    immutable_json,
)
from universal_agent.agentd import AgentdApp, AgentdAuthPolicy, AgentdHttpServer, AgentdServerConfig
from universal_agent.cli import run_cli
from universal_agent.core import JsonMapping, JsonValue
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class RemoteCliBackend:
    def __init__(self) -> None:
        self.inspect_calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls += 1
        assert capability == "inspect_workload"
        name = str(arguments.get("name") or "example")
        resource = name if "/" in name else f"deployment/{name}"
        return immutable_json({"resource": resource, "healthy": True})

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload through remote CLI test",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Remote CLI test finished")


def cli_profile() -> AgentProfile:
    domain = DomainConfig("kubernetes", "0.2.0")
    return AgentProfile(
        "production-operator",
        "1.0.0",
        "Production Kubernetes operator",
        domain,
        RuntimeConfig(environment=immutable_json({"environment": "staging"}), domain=domain),
        (domain,),
    )


def build_app(
    decisions: list[Decision],
    *,
    auth: AgentdAuthPolicy | None = None,
) -> tuple[AgentdApp, RemoteCliBackend]:
    backend = RemoteCliBackend()
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
        profiles=(cli_profile(),),
        config=cli_profile().runtime,
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


@pytest.mark.asyncio
async def test_cli_api_url_reads_agentd_health_without_local_profile() -> None:
    app, _ = build_app([])
    output = StringIO()

    with running_server(app) as base_url:
        status = await run_cli(
            [
                "--api-url",
                base_url,
                "--profile-config",
                "missing-profile.json",
                "health",
            ],
            stdout=output,
        )

    assert status == 0
    assert read_json(output) == {"status": "ok", "service": "universal-agent-runtime"}


@pytest.mark.asyncio
async def test_cli_api_url_runs_goal_and_reads_remote_session() -> None:
    app, backend = build_app([inspect_workload(), finish()])
    run_output = StringIO()
    session_output = StringIO()
    events_output = StringIO()

    with running_server(app) as base_url:
        run_status = await run_cli(
            [
                "--api-url",
                base_url,
                "run",
                "production-operator",
                "Verify workload through agentd",
                "--success",
                "healthy=true",
                "--success",
                'resource="deployment/example"',
            ],
            stdout=run_output,
        )
        run_payload = read_json(run_output)
        result = object_value(run_payload["result"])
        session_id = str(result["session_id"])
        session_status = await run_cli(
            ["--api-url", base_url, "session", "show", session_id],
            stdout=session_output,
        )
        events_status = await run_cli(
            ["--api-url", base_url, "session", "events", session_id, "--limit", "20"],
            stdout=events_output,
        )

    assert run_status == 0
    assert session_status == 0
    assert events_status == 0
    assert result["status"] == "completed"
    assert object_value(read_json(session_output)["satisfied_criteria"]) == {
        "healthy": True,
        "resource": "deployment/example",
    }
    events = array_value(read_json(events_output)["events"])
    assert events[-1]["type"] == "GoalCompleted"
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_api_url_sends_bearer_token_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = build_app([], auth=AgentdAuthPolicy("server-token"))
    output = StringIO()
    monkeypatch.setenv("AGENTD_API_TOKEN", "server-token")

    with running_server(app) as base_url:
        status = await run_cli(
            [
                "--api-url",
                base_url,
                "--api-token-env",
                "AGENTD_API_TOKEN",
                "config",
                "show",
            ],
            stdout=output,
        )

    assert status == 0
    assert array_value(read_json(output)["domains"])[0]["name"] == "kubernetes"
    assert "server-token" not in output.getvalue()


@pytest.mark.asyncio
async def test_cli_api_url_maps_agentd_error_to_cli_error() -> None:
    app, _ = build_app([], auth=AgentdAuthPolicy("server-token"))
    error = StringIO()

    with running_server(app) as base_url:
        status = await run_cli(
            ["--api-url", base_url, "config", "show"],
            stderr=error,
        )

    assert status == 2
    payload = json.loads(error.getvalue())
    assert payload["error"]["code"] == "unauthorized"
    assert "authentication required" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_cli_api_url_rejects_local_only_commands() -> None:
    app, _ = build_app([])
    error = StringIO()

    with running_server(app) as base_url:
        status = await run_cli(["--api-url", base_url, "serve"], stderr=error)

    assert status == 2
    assert "command does not support --api-url: serve" in error.getvalue()


def read_json(buffer: StringIO) -> dict[str, Any]:
    loaded = json.loads(buffer.getvalue())
    assert isinstance(loaded, dict)
    return loaded


def object_value(value: JsonValue) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def array_value(value: JsonValue) -> list[dict[str, Any]]:
    assert isinstance(value, list)
    for item in value:
        assert isinstance(item, dict)
    return cast(list[dict[str, Any]], value)
