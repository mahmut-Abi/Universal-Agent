"""HTTP coverage for the kubernetes operator routes."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from universal_agent.agentd import AgentdApp
from universal_agent.agentd.server import build_agentd_asgi_app
from universal_agent.core import (
    Decision,
    DecisionType,
    JsonMapping,
    JsonValue,
    immutable_json,
)
from universal_agent.domain import DomainLoader, RuntimeBuilder
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.host import DomainConfig, RuntimeConfig
from universal_agent.model import ScriptedModelAdapter
from universal_agent.profile import AgentProfile
from universal_agent.runtime import AgentRuntime, InMemoryEventSink, RuntimeAPI
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore


class Backend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        name = str(arguments.get("name") or "example")
        payload: dict[str, JsonValue] = {
            "resource": f"deployment/{name}",
            "kind": "Deployment",
            "healthy": True,
            "desired_replicas": 3,
            "ready_replicas": 3,
        }
        if "namespace" in arguments:
            payload["namespace"] = str(arguments["namespace"])
        return immutable_json(payload)

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        name = str(arguments.get("name") or "example")
        return immutable_json({"resource": f"deployment/{name}", "scaled": True})


def build_app(
    decisions: tuple[Decision, ...] = (),
    profiles: tuple[AgentProfile, ...] = (),
) -> AgentdApp:
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(Backend(), Backend()))
    )
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=store,
        components=components,
        event_sink=events,
    )
    service = RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
        profiles=profiles,
    )
    return AgentdApp(service)


def operator_profile() -> AgentProfile:
    domain = DomainConfig("kubernetes", "0.2.0")
    return AgentProfile(
        "production-operator",
        "1.0.0",
        "Production Kubernetes operator",
        domain,
        RuntimeConfig(environment=immutable_json({"environment": "staging"}), domain=domain),
        (domain,),
    )


@pytest.mark.integration
def test_kubernetes_preflight_route_runs_read_only_checks() -> None:
    client = TestClient(build_agentd_asgi_app(build_app()))

    response = client.post(
        "/v1/kubernetes/preflight",
        json={"workload": "api", "namespace": "prod"},
    )
    payload = response.json()
    checks = {item["name"]: item for item in payload["checks"]}

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["model"]["provider"] == "scripted"
    assert payload["domain"]["name"] == "kubernetes"
    assert checks["kubernetes_domain"]["status"] == "ok"
    assert checks["workload_inspection"]["status"] == "ok"


@pytest.mark.integration
def test_kubernetes_preflight_route_requires_workload() -> None:
    client = TestClient(build_agentd_asgi_app(build_app()))

    response = client.post("/v1/kubernetes/preflight", json={})

    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "bad_request",
        "message": "workload is required",
    }


@pytest.mark.integration
def test_kubernetes_run_route_reaches_completion() -> None:
    decisions = (
        Decision(
            DecisionType.EXECUTE,
            "Inspect workload",
            capability="inspect_workload",
            target="deployment/api",
            arguments=immutable_json({"name": "api", "namespace": "prod"}),
            expected_observations=("healthy", "resource", "namespace"),
        ),
        Decision(DecisionType.FINISH, "Health verified"),
    )
    client = TestClient(build_agentd_asgi_app(build_app(decisions, profiles=(operator_profile(),))))

    response = client.post(
        "/v1/kubernetes/run",
        json={
            "profile": "production-operator",
            "workload": "api",
            "namespace": "prod",
            "skip_preflight": True,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "completed"
    assert payload["operation"] == {
        "profile": "production-operator",
        "workload": "deployment/api",
        "namespace": "prod",
    }
    run = payload["run"]
    assert isinstance(run, dict)
    assert run["result"]["status"] == "completed"


@pytest.mark.integration
def test_kubernetes_routes_reject_wrong_method() -> None:
    client = TestClient(build_agentd_asgi_app(build_app()))

    response = client.get("/v1/kubernetes/preflight")

    assert response.status_code == 405
