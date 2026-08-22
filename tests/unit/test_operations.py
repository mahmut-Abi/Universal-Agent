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
    build_opentelemetry_trace_export,
    build_runtime_cost,
    build_runtime_logs,
    build_runtime_metrics,
    build_runtime_trace_spans,
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


def test_runtime_logs_project_redacted_structured_records() -> None:
    events = (
        event(
            "event-1",
            "PolicyChecked",
            action_id="action-1",
            data={
                "effect": "deny",
                "arguments": {
                    "name": "example",
                    "api_token": "secret-token",
                    "nested": {"password": "secret-password"},
                },
            },
            occurred_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        ),
        event(
            "event-2",
            "ActionStarted",
            action_id="action-2",
            data={"capability": "inspect_workload", "tool_name": "kubernetes_inspect"},
            occurred_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        ),
    )

    logs = build_runtime_logs(events)

    assert [record.log_id for record in logs] == ["event-2", "event-1"]
    assert logs[0].level == "info"
    assert logs[0].message == "action started: inspect_workload via kubernetes_inspect"
    assert logs[1].level == "error"
    assert logs[1].message == "policy checked: deny"
    arguments = logs[1].data["arguments"]
    assert isinstance(arguments, dict)
    assert arguments["name"] == "example"
    assert arguments["api_token"] == "[REDACTED]"
    nested = arguments["nested"]
    assert isinstance(nested, dict)
    assert nested["password"] == "[REDACTED]"


def test_runtime_trace_spans_project_session_and_action_tree() -> None:
    events = (
        event(
            "event-1",
            "GoalCreated",
            occurred_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        ),
        event(
            "event-2",
            "PolicyChecked",
            action_id="action-1",
            data={
                "effect": "allow",
                "policy": "allow-safe-read",
                "capability": "inspect_workload",
                "tool_name": "kubernetes_inspect_workload",
                "arguments": {"name": "example", "api_token": "secret-token"},
            },
            occurred_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        ),
        event(
            "event-3",
            "ActionStarted",
            action_id="action-1",
            data={"capability": "inspect_workload", "tool_name": "kubernetes_inspect_workload"},
            occurred_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
        ),
        event(
            "event-4",
            "ActionCompleted",
            action_id="action-1",
            data={"status": "succeeded"},
            occurred_at=datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC),
        ),
        event(
            "event-5",
            "GoalCompleted",
            occurred_at=datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC),
        ),
    )

    spans = build_runtime_trace_spans(events)

    assert [span.name for span in spans] == [
        "runtime.session",
        "runtime.action.inspect_workload",
    ]
    root, action = spans
    assert root.trace_id == "trace:session-1"
    assert root.parent_span_id is None
    assert root.status == "ok"
    assert root.duration_ms == 5000
    assert root.attributes["event_count"] == 5
    assert action.trace_id == root.trace_id
    assert action.parent_span_id == root.span_id
    assert action.action_id == ActionId("action-1")
    assert action.status == "ok"
    assert action.duration_ms == 2000
    assert action.attributes["policy"] == "allow-safe-read"
    arguments = action.attributes["arguments"]
    assert isinstance(arguments, dict)
    assert arguments["name"] == "example"
    assert arguments["api_token"] == "[REDACTED]"
    assert action.attributes["event_types"] == [
        "PolicyChecked",
        "ActionStarted",
        "ActionCompleted",
    ]


def test_opentelemetry_trace_export_projects_otlp_json_payload() -> None:
    events = (
        event(
            "event-1",
            "GoalCreated",
            occurred_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        ),
        event(
            "event-2",
            "ActionStarted",
            action_id="action-1",
            data={
                "capability": "inspect_workload",
                "tool_name": "kubernetes_inspect_workload",
                "arguments": {"api_token": "secret-token"},
            },
            occurred_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        ),
        event(
            "event-3",
            "ActionCompleted",
            action_id="action-1",
            data={"status": "succeeded"},
            occurred_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
        ),
        event(
            "event-4",
            "GoalCompleted",
            occurred_at=datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC),
        ),
    )
    spans = build_runtime_trace_spans(events)

    payload = build_opentelemetry_trace_export(
        spans,
        service_name="universal-agent-test",
        scope_version="0.2.0",
    )

    resource_spans = payload["resourceSpans"]
    assert isinstance(resource_spans, list)
    resource_span = resource_spans[0]
    assert isinstance(resource_span, dict)
    resource = resource_span["resource"]
    assert isinstance(resource, dict)
    assert resource["attributes"] == [
        {"key": "service.name", "value": {"stringValue": "universal-agent-test"}}
    ]
    scope_spans = resource_span["scopeSpans"]
    assert isinstance(scope_spans, list)
    scope_span = scope_spans[0]
    assert isinstance(scope_span, dict)
    assert scope_span["scope"] == {"name": "universal-agent-runtime", "version": "0.2.0"}
    exported_spans = scope_span["spans"]
    assert isinstance(exported_spans, list)
    exported_root, exported_action = exported_spans
    assert isinstance(exported_root, dict)
    assert isinstance(exported_action, dict)
    trace_id = exported_root["traceId"]
    span_id = exported_root["spanId"]
    assert isinstance(trace_id, str)
    assert isinstance(span_id, str)
    assert len(trace_id) == 32
    assert len(span_id) == 16
    assert exported_action["parentSpanId"] == span_id
    assert exported_action["kind"] == "SPAN_KIND_CLIENT"
    assert exported_action["status"] == {"code": "STATUS_CODE_OK", "message": "ok"}
    attributes = exported_action["attributes"]
    assert isinstance(attributes, list)
    assert {
        "key": "runtime.session_id",
        "value": {"stringValue": "session-1"},
    } in attributes
    assert {
        "key": "arguments",
        "value": {
            "kvlistValue": {
                "values": [{"key": "api_token", "value": {"stringValue": "[REDACTED]"}}]
            }
        },
    } in attributes


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
        "structured_logs",
        "traces",
        "audit",
        "policy_denials",
        "recovery",
        "cost_tracking",
    ]
    assert next(check for check in report.checks if check.name == "event_stream").status == "warn"
    assert next(check for check in report.checks if check.name == "traces").status == "warn"


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
