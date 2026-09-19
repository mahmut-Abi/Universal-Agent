"""HTTP coverage for the workspace domain routes."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from starlette.testclient import TestClient

from universal_agent.agentd import AgentdApp
from universal_agent.agentd.server import build_agentd_asgi_app
from universal_agent.core import (
    Decision,
    DecisionType,
    immutable_json,
)
from universal_agent.domain import DomainLoader, RuntimeBuilder
from universal_agent.domains.workspace import WorkspaceDomain
from universal_agent.model import ScriptedModelAdapter
from universal_agent.runtime import AgentRuntime, InMemoryEventSink, RuntimeAPI
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore


def build_app(workspace_root: str) -> AgentdApp:
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    decisions = (
        Decision(
            DecisionType.EXECUTE,
            "Create the requested file",
            capability="create_file",
            target="file/notes.txt",
            arguments=immutable_json({"path": "notes.txt", "content": "hello"}),
            expected_observations=("created",),
        ),
        Decision(DecisionType.FINISH, "File created"),
    )
    components = RuntimeBuilder().build(DomainLoader().load(WorkspaceDomain(Path(workspace_root))))
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(list(decisions)),
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
def test_workspace_run_route_executes_goal_and_returns_evidence() -> None:
    with TemporaryDirectory() as tmpdir:
        client = TestClient(build_agentd_asgi_app(build_app(tmpdir)))

        response = client.post(
            "/v1/workspace/run",
            json={
                "goal": "Create notes.txt",
                "task": "Create notes.txt",
                "criteria": [{"key": "created", "expected": True}],
                "task_criteria": ["created"],
            },
        )
        payload = response.json()

        assert response.status_code == 200
        assert payload["status"] == "completed"
        assert payload["error_code"] is None
        subjects = {item["claim"] for item in payload["evidence"]}
        assert "created" in subjects
        assert (Path(tmpdir) / "notes.txt").read_text() == "hello"


@pytest.mark.integration
def test_workspace_run_route_requires_goal() -> None:
    with TemporaryDirectory() as tmpdir:
        client = TestClient(build_agentd_asgi_app(build_app(tmpdir)))

        response = client.post("/v1/workspace/run", json={})

        assert response.status_code == 400
        assert response.json()["error"] == {
            "code": "bad_request",
            "message": "goal is required",
        }


@pytest.mark.integration
def test_workspace_evidence_route_requires_session() -> None:
    with TemporaryDirectory() as tmpdir:
        client = TestClient(build_agentd_asgi_app(build_app(tmpdir)))

        response = client.post("/v1/workspace/evidence", json={})

        assert response.status_code == 400
        assert response.json()["error"]["message"] == "session_id is required"


@pytest.mark.integration
def test_workspace_evidence_route_returns_recorded_claims() -> None:
    with TemporaryDirectory() as tmpdir:
        app = build_app(tmpdir)
        client = TestClient(build_agentd_asgi_app(app))

        run = client.post(
            "/v1/workspace/run",
            json={
                "goal": "Create notes.txt",
                "criteria": [{"key": "created", "expected": True}],
                "task_criteria": ["created"],
            },
        ).json()
        session_id = run["session_id"]

        evidence = client.post("/v1/workspace/evidence", json={"session_id": session_id})
        payload = evidence.json()

        assert evidence.status_code == 200
        claims = {item["claim"] for item in payload["evidence"]}
        assert "created" in claims


@pytest.mark.integration
def test_workspace_route_rejects_get_method() -> None:
    with TemporaryDirectory() as tmpdir:
        client = TestClient(build_agentd_asgi_app(build_app(tmpdir)))

        response = client.get("/v1/workspace/run")

        assert response.status_code == 405
