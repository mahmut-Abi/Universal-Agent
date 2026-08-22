from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType

from universal_agent.core import (
    ActionId,
    GoalId,
    GoalStatus,
    SessionId,
    TaskId,
    TaskStatus,
)
from universal_agent.operations import (
    build_audit_records,
    build_doctor_report,
    build_runtime_cost,
    build_runtime_metrics,
)
from universal_agent.runtime import RuntimeEventView, SessionSummaryView


def session(status: GoalStatus = GoalStatus.COMPLETED) -> SessionSummaryView:
    return SessionSummaryView(
        session_id=SessionId("session-1"),
        goal_id=GoalId("goal-1"),
        goal_description="Restore workload health",
        goal_status=status,
        current_task_id=TaskId("task-1"),
        current_task_description="Inspect workload",
        current_task_status=TaskStatus.COMPLETED,
        iteration=3,
        task_count=1,
        pending_action=False,
        termination_reason="done",
        error_code=None,
        domain_name="kubernetes",
        domain_version="0.2.0",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def event(
    event_id: str,
    event_type: str,
    *,
    action_id: str | None = None,
    data: dict[str, object] | None = None,
    occurred_at: datetime | None = None,
) -> RuntimeEventView:
    return RuntimeEventView(
        event_id=event_id,
        type=event_type,
        session_id=SessionId("session-1"),
        goal_id=GoalId("goal-1"),
        task_id=TaskId("task-1"),
        action_id=None if action_id is None else ActionId(action_id),
        data=MappingProxyType(dict(data or {})),
        occurred_at=occurred_at or datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_runtime_metrics_are_derived_from_sessions_and_events() -> None:
    events = (
        event("event-1", "PolicyChecked", action_id="action-1", data={"effect": "allow"}),
        event("event-2", "ActionStarted", action_id="action-1"),
        event("event-3", "ActionCompleted", action_id="action-1", data={"status": "succeeded"}),
        event("event-4", "ConfirmationRequired", action_id="action-2"),
        event("event-5", "RecoveryPlanned", action_id="action-3"),
        event(
            "event-6",
            "ModelUsageRecorded",
            data={
                "provider": "scripted",
                "model": "fixture-model",
                "input_tokens": 100,
                "output_tokens": 25,
                "estimated_cost_micros": 42,
                "currency": "USD",
            },
        ),
    )

    metrics = build_runtime_metrics((session(), session(GoalStatus.WAITING)), events)

    assert metrics.session_count == 2
    assert metrics.completed_goal_count == 1
    assert metrics.waiting_session_count == 1
    assert metrics.event_count == 6
    assert metrics.action_started_count == 1
    assert metrics.action_completed_count == 1
    assert metrics.tool_failure_count == 0
    assert metrics.confirmation_required_count == 1
    assert metrics.human_intervention_count == 1
    assert metrics.recovery_planned_count == 1
    assert metrics.model_call_count == 1
    assert metrics.model_input_token_count == 100
    assert metrics.model_output_token_count == 25
    assert metrics.model_total_token_count == 125
    assert metrics.model_estimated_cost_micros == 42


def test_runtime_cost_is_grouped_by_model_usage_events() -> None:
    events = (
        event(
            "event-1",
            "ModelUsageRecorded",
            data={
                "provider": "openai",
                "model": "gpt-5-codex",
                "input_tokens": 100,
                "output_tokens": 25,
                "estimated_cost_micros": 50,
                "currency": "USD",
            },
        ),
        event(
            "event-2",
            "ModelUsageRecorded",
            data={
                "provider": "openai",
                "model": "gpt-5-codex",
                "input_tokens": 50,
                "output_tokens": 10,
                "estimated_cost_micros": 20,
                "currency": "USD",
            },
        ),
        event(
            "event-3",
            "ModelUsageRecorded",
            data={
                "provider": "local",
                "model": "test-model",
                "input_tokens": 5,
                "output_tokens": 1,
                "estimated_cost_micros": 0,
                "currency": "USD",
            },
        ),
    )

    cost = build_runtime_cost(events)

    assert cost.model_call_count == 3
    assert cost.input_tokens == 155
    assert cost.output_tokens == 36
    assert cost.total_tokens == 191
    assert cost.estimated_cost_micros == 70
    assert cost.currency == "USD"
    assert [(item.provider, item.model, item.call_count) for item in cost.by_model] == [
        ("local", "test-model", 1),
        ("openai", "gpt-5-codex", 2),
    ]
    assert cost.by_model[1].total_tokens == 185


def test_doctor_report_aggregates_readiness_and_event_stream_checks() -> None:
    report = build_doctor_report(
        health_status="ok",
        ready=True,
        ready_reason="ready",
        domain_count=1,
        capability_count=2,
        tool_count=2,
        sessions=(session(),),
        events=(),
    )

    assert report.status == "warn"
    assert [check.name for check in report.checks] == [
        "service_health",
        "readiness",
        "catalog",
        "session_store",
        "event_stream",
        "policy_denials",
        "recovery",
        "cost_tracking",
    ]
    assert next(check for check in report.checks if check.name == "event_stream").status == "warn"


def test_audit_records_include_only_side_effecting_policy_checks() -> None:
    events = (
        event(
            "event-1",
            "PolicyChecked",
            action_id="action-1",
            data={
                "effect": "require_confirmation",
                "policy": "kubernetes-scale-safety",
                "capability": "scale_workload",
                "tool_name": "kubernetes_scale_workload",
                "side_effect": "reversible",
                "risk": "medium",
            },
            occurred_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        ),
        event(
            "event-2",
            "ActionCompleted",
            action_id="action-1",
            data={"status": "succeeded"},
            occurred_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        ),
        event(
            "event-3",
            "PolicyChecked",
            action_id="action-2",
            data={"side_effect": "none", "effect": "allow"},
        ),
    )

    records = build_audit_records(events)

    assert len(records) == 1
    assert records[0].record_id == "event-1"
    assert records[0].capability == "scale_workload"
    assert records[0].policy_effect == "require_confirmation"
    assert records[0].status == "confirmation_required"
    assert records[0].completed_at == datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
