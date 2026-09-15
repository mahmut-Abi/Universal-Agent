"""Unit tests for the session event timeline projection and console route.

The timeline groups runtime events into correlated steps (decision -> policy
-> action -> observation -> evidence) keyed by stable action identifiers, so
operator surfaces can answer "why did the agent do this?" from one payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

import pytest

from universal_agent.agentd.console_routes import handle_console_route
from universal_agent.core import ActionId, GoalId, JsonMapping, SessionId, TaskId
from universal_agent.runtime.api import RuntimeEventBatch, RuntimeEventView
from universal_agent.service.timeline import timeline_body
from universal_agent.state import StateNotFoundError

NOW = datetime.now(UTC)
SESSION = SessionId("session-1")


def _ev(
    index: int,
    event_type: str,
    *,
    goal: str = "goal-1",
    task: str = "task-1",
    action: str | None = None,
    data: dict[str, Any] | None = None,
) -> RuntimeEventView:
    return RuntimeEventView(
        f"e{index}",
        event_type,
        SESSION,
        GoalId(goal),
        TaskId(task),
        ActionId(action) if action is not None else None,
        MappingProxyType(data or {}),
        NOW,
    )


def _obj(value: object) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _arr(value: object) -> list[Any]:
    assert isinstance(value, list)
    return value


def _happy_path_events() -> tuple[RuntimeEventView, ...]:
    return (
        _ev(1, "GoalCreated"),
        _ev(2, "TaskCreated"),
        _ev(
            3,
            "DecisionGenerated",
            action="a1",
            data={
                "decision_type": "execute",
                "capability": "scale_deployment",
                "target": "deployment/api",
                "arguments": {"replicas": 3},
                "reason": "scale up",
                "expected_observations": ("replicas",),
            },
        ),
        _ev(
            4,
            "PolicyChecked",
            action="a1",
            data={"effect": "allow", "policy": "scaling-policy"},
        ),
        _ev(5, "ActionStarted", action="a1", data={"tool_name": "kubectl_scale", "attempt": 1}),
        _ev(6, "ActionCompleted", action="a1", data={"status": "succeeded", "error_code": None}),
        _ev(
            7,
            "ObservationReceived",
            action="a1",
            data={"observation_id": "obs-1", "status": "succeeded"},
        ),
        _ev(
            8,
            "EvidenceRecorded",
            action="a1",
            data={"evidence_id": "ev-1", "claim": "deployment scaled"},
        ),
        _ev(9, "WorldModelUpdated", action="a1", data={"evidence_count": 1}),
        _ev(10, "GoalCompleted"),
    )


@pytest.mark.unit
def test_timeline_groups_full_decision_chain_into_one_step() -> None:
    body = _obj(timeline_body(_happy_path_events()))
    assert body["step_count"] == 2

    steps = _arr(body["steps"])
    context, step = (_obj(steps[0]), _obj(steps[1]))
    assert context["label"] == "GoalCreated"
    assert context["event_count"] == 2

    assert step["label"] == "Decision: scale_deployment (execute)"
    assert step["action_id"] == "a1"
    assert step["goal_id"] == "goal-1"
    assert step["task_id"] == "task-1"
    assert step["policy_effect"] == "allow"
    assert step["observation_id"] == "obs-1"
    assert step["evidence_ids"] == ["ev-1"]

    decision = _obj(step["decision"])
    assert decision["target"] == "deployment/api"
    assert decision["arguments"] == {"replicas": 3}
    assert step["event_count"] == 8
    assert "GoalCompleted" in step["event_types"]


@pytest.mark.unit
def test_timeline_splits_retry_attempts_by_action_id() -> None:
    events = (
        _ev(
            1,
            "DecisionGenerated",
            action="a1",
            data={"decision_type": "execute", "capability": "inspect_pod"},
        ),
        _ev(2, "ActionStarted", action="a1", data={"tool_name": "kubectl_get", "attempt": 1}),
        _ev(3, "ActionCompleted", action="a1", data={"status": "failed", "error_code": "timeout"}),
        _ev(
            4,
            "DecisionGenerated",
            action="a2",
            data={"decision_type": "execute", "capability": "inspect_pod"},
        ),
        _ev(5, "ActionStarted", action="a2", data={"tool_name": "kubectl_get", "attempt": 2}),
    )
    body = _obj(timeline_body(events))
    assert body["step_count"] == 2
    steps = _arr(body["steps"])
    first, second = (_obj(steps[0]), _obj(steps[1]))
    assert first["action_id"] == "a1"
    assert first["event_count"] == 3
    assert second["action_id"] == "a2"
    assert second["evidence_ids"] == []


@pytest.mark.unit
def test_timeline_surfaces_policy_denial() -> None:
    events = (
        _ev(
            1,
            "DecisionGenerated",
            action="a1",
            data={"decision_type": "execute", "capability": "delete_pod"},
        ),
        _ev(
            2,
            "PolicyChecked",
            action="a1",
            data={"effect": "deny", "policy": "destructive-policy"},
        ),
    )
    body = _obj(timeline_body(events))
    step = _obj(_arr(body["steps"])[0])
    assert step["policy_effect"] == "deny"
    assert step["event_count"] == 2
    assert _obj(step["decision"])["capability"] == "delete_pod"


@pytest.mark.unit
def test_timeline_handles_empty_and_orphan_events() -> None:
    assert _obj(timeline_body(()))["step_count"] == 0

    orphan = _obj(timeline_body((_ev(1, "SessionPaused"),)))
    assert orphan["step_count"] == 1
    step = _obj(_arr(orphan["steps"])[0])
    assert step["label"] == "SessionPaused"
    assert step["action_id"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_console_session_timeline_route_serves_projection() -> None:
    class StubService:
        async def stream_events(self, session_id: SessionId) -> RuntimeEventBatch:
            assert str(session_id) == "session-1"
            return RuntimeEventBatch(_happy_path_events(), None)

    response = await handle_console_route(
        StubService(),  # type: ignore[arg-type]
        None,
        _StubRequest(),
        "GET",
        "/console/sessions/session-1/timeline",
    )
    assert response is not None
    assert response.status_code == 200
    body = _obj(response.body)
    assert body["step_count"] == 2
    step = _obj(_arr(body["steps"])[1])
    assert _obj(step["decision"])["capability"] == "scale_deployment"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_console_session_timeline_route_reports_missing_session() -> None:
    class StubService:
        async def stream_events(self, session_id: SessionId) -> RuntimeEventBatch:
            raise StateNotFoundError(f"session not found: {session_id}")

    response = await handle_console_route(
        StubService(),  # type: ignore[arg-type]
        None,
        _StubRequest(),
        "GET",
        "/console/sessions/session-missing/timeline",
    )
    assert response is not None
    assert response.status_code == 404


@dataclass
class _StubRequest:
    body: dict[str, JsonMapping] = field(default_factory=dict)
