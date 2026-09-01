"""Integration coverage for the memory CRUD API and CLI subcommands."""

from __future__ import annotations

from io import StringIO
from types import MappingProxyType

import pytest
from starlette.testclient import TestClient

from universal_agent.agentd import AgentdApp
from universal_agent.agentd.server import build_agentd_asgi_app
from universal_agent.core import (
    Decision,
    DecisionType,
    JsonMapping,
    immutable_json,
)
from universal_agent.domain import DomainLoader, RuntimeBuilder
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.host.config import DomainConfig, RuntimeConfig
from universal_agent.model import ScriptedModelAdapter
from universal_agent.profile import AgentProfile
from universal_agent.runtime import AgentRuntime, InMemoryEventSink, RuntimeAPI
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore
from universal_agent_cli import run_cli


class Backend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json(
            {
                "resource": "deployment/example",
                "kind": "Deployment",
                "healthy": True,
                "desired_replicas": 3,
                "ready_replicas": 3,
            }
        )

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"resource": "deployment/example", "scaled": True})


def build_service(
    decisions: tuple[Decision, ...] = (),
    profiles: tuple[AgentProfile, ...] = (),
) -> RuntimeService:
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(Backend(), Backend()))
    )
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(tuple(decisions)),
        state_store=store,
        components=components,
        event_sink=events,
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
        profiles=profiles,
    )


def build_client() -> TestClient:
    return TestClient(build_agentd_asgi_app(AgentdApp(build_service())))


@pytest.mark.integration
def test_memory_api_crud_round_trip() -> None:
    with build_client() as client:
        created = client.post(
            "/v1/memory",
            json={
                "kind": "semantic",
                "subject": "deployment/example",
                "content": "Prefers rolling updates",
                "scope": "kubernetes",
                "confidence": 0.9,
            },
        )
        assert created.status_code == 201
        body = created.json()
        memory_id = body["memory_id"]
        assert isinstance(memory_id, str)
        assert body["kind"] == "semantic"
        assert body["content"] == "Prefers rolling updates"

        detail = client.get(f"/v1/memory/{memory_id}")
        assert detail.status_code == 200
        assert detail.json()["subject"] == "deployment/example"

        listing = client.get("/v1/memory")
        assert listing.status_code == 200
        memories = listing.json()["memories"]
        assert any(item["memory_id"] == memory_id for item in memories)

        deleted = client.delete(f"/v1/memory/{memory_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True, "memory_id": memory_id}

        assert client.get(f"/v1/memory/{memory_id}").status_code == 404
        assert client.delete(f"/v1/memory/{memory_id}").status_code == 404


@pytest.mark.integration
def test_memory_api_rejects_invalid_kind_and_missing_record() -> None:
    with build_client() as client:
        invalid = client.post(
            "/v1/memory",
            json={"kind": "bogus", "subject": "s", "content": "c"},
        )
        assert invalid.status_code == 400

        assert client.get("/v1/memory/memory-missing").status_code == 404


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cli_memory_subcommands_round_trip() -> None:
    service = build_service()
    out = StringIO()

    add_status = await run_cli(
        [
            "memory",
            "add",
            "--kind",
            "preference",
            "--subject",
            "deployment/example",
            "--content",
            "Prefers canary rollouts",
        ],
        service=service,
        stdout=out,
    )
    assert add_status == 0
    import json as jsonlib

    added = jsonlib.loads(out.getvalue())
    memory_id = added["memory_id"]
    assert added["kind"] == "preference"

    out = StringIO()
    get_status = await run_cli(["memory", "get", memory_id], service=service, stdout=out)
    assert get_status == 0
    assert jsonlib.loads(out.getvalue())["content"] == "Prefers canary rollouts"

    out = StringIO()
    list_status = await run_cli(["memory"], service=service, stdout=out)
    assert list_status == 0
    memories = jsonlib.loads(out.getvalue())["memories"]
    assert any(item["memory_id"] == memory_id for item in memories)

    out = StringIO()
    delete_status = await run_cli(["memory", "delete", memory_id], service=service, stdout=out)
    assert delete_status == 0

    out = StringIO()
    missing_status = await run_cli(["memory", "get", memory_id], service=service, stdout=out)
    assert missing_status == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cli_chat_runs_goals_per_line(monkeypatch: pytest.MonkeyPatch) -> None:

    decisions = (
        Decision(
            DecisionType.EXECUTE,
            "Inspect workload",
            capability="inspect_workload",
            target="deployment/example",
            arguments=MappingProxyType({"name": "example"}),
            expected_observations=("healthy",),
        ),
        Decision(DecisionType.FINISH, "Evidence is present"),
    )
    domain = DomainConfig("kubernetes", "0.2.0")
    profile = AgentProfile(
        "local-kubernetes",
        "1.0.0",
        "Local Kubernetes operator",
        domain,
        RuntimeConfig(environment=immutable_json({"environment": "local"}), domain=domain),
        (domain,),
    )
    service = build_service(decisions=decisions, profiles=(profile,))

    lines = iter(["inspect the workload", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(lines))

    out = StringIO()
    status = await run_cli(["chat"], service=service, stdout=out)

    assert status == 0
    text = out.getvalue()
    assert "Universal Agent chat" in text
    assert "[completed]" in text
    assert "bye" in text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cli_chat_rejects_unknown_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    service = build_service()
    lines = iter(["anything", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(lines))

    out = StringIO()
    err = StringIO()
    status = await run_cli(["chat"], service=service, stdout=out, stderr=err)

    assert status == 2
    assert "unknown profile" in err.getvalue()
