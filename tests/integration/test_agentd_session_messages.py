"""Session continuation endpoint tests (POST /v1/sessions/{id}/messages).

A finished session gains a follow-up user message: the runtime hydrates the
session, appends a task carrying the message, and re-enters the loop with a
fresh iteration budget. Policy/evaluation semantics are identical to a run.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest

from universal_agent.agentd import AgentdApp
from universal_agent.agentd.http import HttpRequest
from universal_agent.core import (
    Decision,
    DecisionType,
    JsonMapping,
    JsonValue,
    immutable_json,
)
from universal_agent.domain import DomainLoader, RuntimeBuilder
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.model import ScriptedModelAdapter
from universal_agent.runtime import AgentRuntime, InMemoryEventSink, RuntimeAPI
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore

pytestmark = pytest.mark.integration


class _Backend:
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


def _inspect() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def _finish() -> Decision:
    return Decision(DecisionType.FINISH, "Health verified")


def build_app(decisions: tuple[Decision, ...]) -> AgentdApp:
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(_Backend(), _Backend()))
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
    )
    return AgentdApp(service)


def _request(
    method: str, path: str, body: Mapping[str, JsonValue] | None = None
) -> HttpRequest:
    return HttpRequest(
        method=method,
        path=path,
        body={} if body is None else dict(body),
    )


@pytest.mark.asyncio
async def test_session_messages_continue_completed_session() -> None:
    app = build_app(
        (
            _inspect(),
            _finish(),
            # continuation turn: inspect -> evaluation -> finish is accepted
            _inspect(),
            _finish(),
        )
    )

    created = await app.handle(
        _request(
            "POST",
            "/v1/sessions",
            {
                "goal": {
                    "description": "Check example deployment",
                    "success_criteria": [{"key": "healthy", "expected": True}],
                },
                "task": {"description": "Inspect", "required_criteria": ["healthy"]},
            },
        )
    )
    assert created is not None and created.status_code == 201
    body = created.body
    result = body.get("result")
    assert isinstance(result, dict)
    session_id = str(result["session_id"])

    continued = await app.handle(
        _request("POST", f"/v1/sessions/{session_id}/messages", {"message": "and now?"})
    )
    assert continued is not None and continued.status_code == 200
    run_body = continued.body
    run_result = run_body.get("result")
    assert isinstance(run_result, dict)
    assert run_result["status"] == "completed"

    events = await app.handle(_request("GET", f"/v1/sessions/{session_id}/events"))
    assert events is not None and events.status_code == 200
    event_payload = events.body.get("events")
    assert isinstance(event_payload, list)
    types = [
        str(item.get("type")) for item in event_payload if isinstance(item, dict)
    ]
    assert "SessionContinued" in types


@pytest.mark.asyncio
async def test_session_messages_rejects_empty_message() -> None:
    app = build_app((_inspect(), _finish()))
    created = await app.handle(
        _request(
            "POST",
            "/v1/sessions",
            {
                "goal": {
                    "description": "Check",
                    "success_criteria": [{"key": "healthy", "expected": True}],
                },
                "task": {"description": "Inspect", "required_criteria": ["healthy"]},
            },
        )
    )
    assert created is not None and created.status_code == 201
    session_id = str(
        cast("dict[str, object]", created.body.get("result"))["session_id"]
    )

    response = await app.handle(
        _request("POST", f"/v1/sessions/{session_id}/messages", {"message": ""})
    )
    assert response is not None and response.status_code == 400


@pytest.mark.asyncio
async def test_session_messages_unknown_session_is_404() -> None:
    app = build_app((_finish(),))
    response = await app.handle(
        _request("POST", "/v1/sessions/session-missing/messages", {"message": "hi"})
    )
    assert response is not None and response.status_code == 404
