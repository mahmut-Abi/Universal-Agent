from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from universal_agent.core import (
    ActionId,
    ErrorCode,
    ExecutionStatus,
    GoalId,
    SessionId,
    TaskId,
)
from universal_agent.evaluation.replay import replay_execution
from universal_agent.runtime import RuntimeEventView


def event(
    event_id: str,
    event_type: str,
    *,
    action_id: str | None = None,
    data: dict[str, object] | None = None,
    session_id: str = "session-1",
    task_id: str = "task-1",
) -> RuntimeEventView:
    return RuntimeEventView(
        event_id=event_id,
        type=event_type,
        session_id=SessionId(session_id),
        goal_id=GoalId("goal-1"),
        task_id=TaskId(task_id),
        action_id=None if action_id is None else ActionId(action_id),
        data=MappingProxyType(dict(data or {})),
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_execution_replay_reconstructs_successful_history_from_events() -> None:
    replay = replay_execution(
        (
            event("event-1", "DomainActivated", data={"domain": "kubernetes"}),
            event("event-2", "GoalCreated"),
            event("event-3", "TaskCreated"),
            event(
                "event-4",
                "DecisionGenerated",
                data={"decision_type": "execute", "reason": "inspect first"},
            ),
            event(
                "event-5",
                "CapabilityResolved",
                action_id="action-1",
                data={
                    "capability": "inspect_workload",
                    "tool_name": "kubernetes_inspect_workload",
                    "domain": "kubernetes",
                    "domain_version": "0.2.0",
                },
            ),
            event(
                "event-6",
                "PolicyChecked",
                action_id="action-1",
                data={"effect": "allow", "policy": "default-allow"},
            ),
            event(
                "event-7",
                "ActionStarted",
                action_id="action-1",
                data={"capability": "inspect_workload", "tool_name": "kubernetes_inspect"},
            ),
            event(
                "event-8",
                "ActionCompleted",
                action_id="action-1",
                data={"status": "succeeded"},
            ),
            event(
                "event-9",
                "ObservationReceived",
                action_id="action-1",
                data={"observation_id": "observation-1", "status": "succeeded"},
            ),
            event(
                "event-10",
                "EvidenceRecorded",
                action_id="action-1",
                data={"evidence_id": "evidence-1", "claim": "healthy"},
            ),
            event("event-11", "WorldModelUpdated", action_id="action-1"),
            event(
                "event-12",
                "EvaluationCompleted",
                action_id="action-1",
                data={"status": "completed", "evaluator": "criteria"},
            ),
            event("event-13", "StateUpdated", action_id="action-1"),
            event(
                "event-14",
                "DecisionGenerated",
                data={"decision_type": "finish", "reason": "verified"},
            ),
            event("event-15", "GoalCompleted", data={"reason": "healthy"}),
        )
    )

    assert replay.session_id == "session-1"
    assert replay.goal_id == "goal-1"
    assert replay.task_ids == ("task-1",)
    assert replay.event_count == 15
    assert [decision.decision_type for decision in replay.decisions] == ["execute", "finish"]
    assert replay.observation_ids == ("observation-1",)
    assert replay.evidence_ids == ("evidence-1",)
    assert replay.terminal_status is ExecutionStatus.COMPLETED
    assert replay.terminal_reason == "healthy"

    action = replay.actions[0]
    assert action.action_id == "action-1"
    assert action.capability == "inspect_workload"
    assert action.policy_effect == "allow"
    assert action.status == "succeeded"
    assert action.observation_ids == ("observation-1",)
    assert action.evidence_ids == ("evidence-1",)
    assert action.evidence_claims == ("healthy",)
    assert action.evaluation_status == "completed"


def test_execution_replay_reconstructs_resource_conflict_failure() -> None:
    replay = replay_execution(
        (
            event("event-1", "GoalCreated"),
            event(
                "event-2",
                "CapabilityResolved",
                action_id="action-1",
                data={
                    "capability": "scale_workload",
                    "tool_name": "kubernetes_scale_workload",
                    "resource_key": "deployment/example",
                },
            ),
            event(
                "event-3",
                "ResourceConflictDetected",
                action_id="action-1",
                data={"resource_key": "deployment/example"},
            ),
            event(
                "event-4",
                "GoalFailed",
                data={"error_code": "resource_conflict", "reason": "resource is locked"},
            ),
        )
    )

    assert replay.terminal_status is ExecutionStatus.FAILED
    assert replay.terminal_error_code is ErrorCode.RESOURCE_CONFLICT
    assert replay.terminal_reason == "resource is locked"
    assert replay.actions[0].capability == "scale_workload"
    assert replay.actions[0].resource_key == "deployment/example"
    assert replay.actions[0].error_code == "resource_conflict"


def test_execution_replay_requires_events() -> None:
    with pytest.raises(ValueError, match="at least one event"):
        replay_execution(())


def test_execution_replay_rejects_mixed_sessions() -> None:
    with pytest.raises(ValueError, match="exactly one session"):
        replay_execution(
            (
                event("event-1", "GoalCreated", session_id="session-1"),
                event("event-2", "GoalCreated", session_id="session-2"),
            )
        )
