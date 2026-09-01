"""Static Web Console frontend assets served by agentd.

The frontend lives in the universal_agent_web client package (a pure HTTP
API client); agentd serves its files for /console routes and degrades to a
fallback page when the package is not installed.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from universal_agent.agentd import AgentdApp
from universal_agent.agentd.server import build_agentd_asgi_app
from universal_agent.core import JsonMapping, immutable_json
from universal_agent.domain import DomainLoader, RuntimeBuilder
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.model import ScriptedModelAdapter
from universal_agent.runtime import AgentRuntime, InMemoryEventSink, RuntimeAPI
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore


def build_app() -> AgentdApp:
    class Backend:
        async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
            return immutable_json(
                {
                    "resource": "deployment/example",
                    "kind": "Deployment",
                    "healthy": True,
                }
            )

        async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
            return immutable_json({"resource": "deployment/example", "scaled": True})

    store = InMemoryStateStore()
    events = InMemoryEventSink()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(Backend(), Backend()))
    )
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(()),
        state_store=store,
        components=components,
        event_sink=events,
    )
    service = RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )
    return AgentdApp(service)


@pytest.mark.integration
def test_console_serves_static_frontend_shell() -> None:
    client = TestClient(build_agentd_asgi_app(build_app()))

    shell = client.get("/console")
    assert shell.status_code == 200
    assert "text/html" in shell.headers["content-type"]
    assert "Universal Agent Web Console" in shell.text
    assert "/console/app.js" in shell.text

    app_js = client.get("/console/app.js")
    assert app_js.status_code == 200
    assert "text/javascript" in app_js.headers["content-type"]
    assert "fetch(" in app_js.text

    styles = client.get("/console/style.css")
    assert styles.status_code == 200
    assert "text/css" in styles.headers["content-type"]

    deep_link = client.get("/console/sessions/session-1")
    assert deep_link.status_code == 200
    assert "Universal Agent Web Console" in deep_link.text


@pytest.mark.integration
def test_console_evaluations_endpoint_reports_configuration() -> None:
    client = TestClient(build_agentd_asgi_app(build_app()))

    payload = client.get("/console/evaluations").json()
    assert payload["status"] == "not_configured"
    assert payload["reports"] == []


@pytest.mark.integration
def test_console_root_serves_frontend_without_runtime_state() -> None:
    client = TestClient(build_agentd_asgi_app(build_app()))

    root = client.get("/")
    assert root.status_code == 200
    assert "text/html" in root.headers["content-type"]
