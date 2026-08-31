from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

import pytest

from universal_agent.core import (
    ActionId,
    ErrorCode,
    GoalId,
    GoalStatus,
    SessionId,
    TaskId,
    TaskStatus,
)
from universal_agent.operations import (
    build_audit_integrity,
    build_audit_records,
    build_doctor_report,
    build_opentelemetry_trace_export,
    build_prometheus_metrics_export,
    build_runtime_cost,
    build_runtime_logs,
    build_runtime_metrics,
    build_runtime_trace_spans,
)
from universal_agent.runtime import RuntimeEventView, SessionSummaryView
from universal_agent.security import (
    SecretResolution,
    SecretResolutionReport,
    SecretResolutionStatus,
)


def session(
    status: GoalStatus = GoalStatus.COMPLETED,
    *,
    task_status: TaskStatus = TaskStatus.COMPLETED,
    pending_action: bool = False,
) -> SessionSummaryView:
    return SessionSummaryView(
        session_id=SessionId("session-1"),
        goal_id=GoalId("goal-1"),
        goal_description="Restore workload health",
        goal_status=status,
        current_task_id=TaskId("task-1"),
        current_task_description="Inspect workload",
        current_task_status=task_status,
        iteration=3,
        task_count=1,
        pending_action=pending_action,
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
    session_id: str = "session-1",
    action_id: str | None = None,
    data: dict[str, object] | None = None,
    occurred_at: datetime | None = None,
) -> RuntimeEventView:
    return RuntimeEventView(
        event_id=event_id,
        type=event_type,
        session_id=SessionId(session_id),
        goal_id=GoalId("goal-1"),
        task_id=TaskId("task-1"),
        action_id=None if action_id is None else ActionId(action_id),
        data=MappingProxyType(dict(data or {})),
        occurred_at=occurred_at or datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.behavior
def test_runtime_metrics_are_derived_from_sessions_and_events() -> None:
    events = (
        event("event-1", "PolicyChecked", action_id="action-1", data={"effect": "allow"}),
        event("event-13", "PolicyChecked", action_id="action-2", data={"effect": "deny"}),
        event("event-2", "ActionStarted", action_id="action-1"),
        event("event-3", "ActionCompleted", action_id="action-1", data={"status": "succeeded"}),
        event("event-4", "DecisionGenerated", data={"decision_type": "execute"}),
        event("event-5", "DecisionValidated", data={"decision_type": "execute"}),
        event("event-6", "DecisionRejected", data={"decision_type": "execute"}),
        event("event-7", "ConfirmationRequired", action_id="action-2"),
        event("event-8", "RecoveryPlanned", action_id="action-3"),
        event(
            "event-9",
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
        event(
            "event-10",
            "ResourceLockAcquired",
            action_id="action-2",
            data={"resource_key": "deployment/example"},
        ),
        event(
            "event-11",
            "ResourceLockReleased",
            action_id="action-2",
            data={"resource_key": "deployment/example"},
        ),
        event(
            "event-12",
            "ResourceConflictDetected",
            action_id="action-4",
            data={"resource_key": "deployment/example"},
        ),
        event(
            "event-14",
            "EvaluationCompleted",
            action_id="action-1",
            data={"status": "completed", "evaluator": "workload-health"},
        ),
        event(
            "event-15",
            "EvaluationCompleted",
            action_id="action-4",
            data={"status": "failed", "evaluator": "workload-health"},
        ),
    )

    metrics = build_runtime_metrics(
        (session(), session(GoalStatus.WAITING, task_status=TaskStatus.WAITING)),
        events,
    )

    assert metrics.session_count == 2
    assert metrics.completed_goal_count == 1
    assert metrics.waiting_session_count == 1
    assert metrics.event_count == 15
    assert metrics.action_started_count == 1
    assert metrics.action_completed_count == 1
    assert metrics.decision_generated_count == 1
    assert metrics.decision_validated_count == 1
    assert metrics.decision_rejected_count == 1
    assert metrics.tool_failure_count == 0
    assert metrics.policy_checked_count == 2
    assert metrics.policy_denial_count == 1
    assert metrics.confirmation_required_count == 1
    assert metrics.human_intervention_count == 1
    assert metrics.recovery_planned_count == 1
    assert metrics.evaluation_count == 2
    assert metrics.evaluation_success_count == 1
    assert metrics.evaluation_failure_count == 1
    assert metrics.current_task_completed_count == 1
    assert metrics.resource_lock_acquired_count == 1
    assert metrics.resource_lock_released_count == 1
    assert metrics.resource_conflict_count == 1
    assert metrics.active_resource_lock_count == 0
    assert metrics.goal_completion_rate == 0.5
    assert metrics.task_success_rate == 0.5
    assert metrics.action_success_rate == 1.0
    assert metrics.tool_failure_rate == 0.0
    assert metrics.policy_denial_rate == 0.5
    assert metrics.recovery_rate == 0.5
    assert metrics.human_intervention_rate == 0.5
    assert metrics.verification_success_rate == 0.5
    assert metrics.model_call_count == 1
    assert metrics.model_input_token_count == 100
    assert metrics.model_output_token_count == 25
    assert metrics.model_total_token_count == 125
    assert metrics.model_estimated_cost_micros == 42


@pytest.mark.unit
def test_prometheus_metrics_export_projects_runtime_metrics_text() -> None:
    metrics = build_runtime_metrics(
        (session(), session(GoalStatus.WAITING)),
        (
            event("event-1", "ActionStarted", action_id="action-1"),
            event("event-2", "ActionCompleted", action_id="action-1"),
            event("event-5", "DecisionGenerated", data={"decision_type": "execute"}),
            event("event-6", "DecisionValidated", data={"decision_type": "execute"}),
            event("event-7", "DecisionRejected", data={"decision_type": "execute"}),
            event("event-8", "EvaluationCompleted", data={"status": "completed"}),
            event(
                "event-4",
                "ResourceLockAcquired",
                action_id="action-2",
                data={"resource_key": "deployment/example"},
            ),
            event(
                "event-3",
                "ModelUsageRecorded",
                data={
                    "provider": "scripted",
                    "model": "fixture-model",
                    "input_tokens": 100,
                    "output_tokens": 25,
                    "estimated_cost_micros": 42,
                },
            ),
        ),
    )

    exported = build_prometheus_metrics_export(metrics)

    assert exported.endswith("\n")
    assert "# TYPE universal_agent_runtime_sessions gauge\n" in exported
    assert "universal_agent_runtime_sessions 2.0\n" in exported
    assert "universal_agent_runtime_waiting_sessions 1.0\n" in exported
    assert "universal_agent_runtime_actions_completed 1.0\n" in exported
    assert "universal_agent_runtime_decisions_generated 1.0\n" in exported
    assert "universal_agent_runtime_decisions_validated 1.0\n" in exported
    assert "universal_agent_runtime_decisions_rejected 1.0\n" in exported
    assert "universal_agent_runtime_evaluations 1.0\n" in exported
    assert "universal_agent_runtime_verification_success_rate 1.0\n" in exported
    assert "universal_agent_runtime_active_resource_locks 1.0\n" in exported
    assert "universal_agent_runtime_model_total_tokens 125.0\n" in exported
    assert "universal_agent_runtime_model_estimated_cost_micros 42.0\n" in exported


@pytest.mark.unit
def test_prometheus_metrics_export_sanitizes_metric_prefix() -> None:
    metrics = build_runtime_metrics((), ())

    exported = build_prometheus_metrics_export(metrics, prefix="123 bad-prefix")

    assert "_123_bad_prefix_sessions 0.0\n" in exported


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
def test_runtime_logs_use_library_case_conversion_for_unknown_events() -> None:
    logs = build_runtime_logs(
        (
            event("event-1", "GoalCreated"),
            event("event-2", "HTTPProbeFailed"),
        )
    )

    assert [record.message for record in logs] == ["goal created", "http probe failed"]


@pytest.mark.unit
def test_audit_integrity_builds_stable_hash_chain() -> None:
    events = (
        event(
            "event-1",
            "PolicyChecked",
            action_id="action-1",
            data={
                "effect": "allow",
                "policy": "safe-mutation",
                "capability": "scale_workload",
                "tool_name": "kubernetes_scale_workload",
                "side_effect": "reversible",
                "risk": "medium",
            },
            occurred_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        ),
        event(
            "event-2",
            "ActionCompleted",
            action_id="action-1",
            data={"status": "succeeded"},
            occurred_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        ),
    )
    records = build_audit_records(events)

    integrity = build_audit_integrity(records)
    repeated = build_audit_integrity(records)

    assert integrity == repeated
    assert integrity.record_count == 1
    assert len(integrity.root_hash) == 64
    assert integrity.records[0].record_id == "event-1"
    assert integrity.records[0].record_hash == integrity.root_hash


@pytest.mark.behavior
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
        "runtime.policy",
    ]
    root, action, policy = spans
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
    assert policy.parent_span_id == action.span_id
    assert policy.action_id == ActionId("action-1")
    assert policy.status == "ok"
    assert policy.attributes["event_type"] == "PolicyChecked"
    assert policy.attributes["policy"] == "allow-safe-read"


@pytest.mark.behavior
def test_runtime_trace_spans_project_decision_model_and_evaluation_phases() -> None:
    events = (
        event(
            "event-1",
            "GoalCreated",
            occurred_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        ),
        event(
            "event-2",
            "DecisionGenerated",
            data={"decision_type": "execute", "reason": "inspect workload"},
            occurred_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        ),
        event(
            "event-3",
            "DecisionValidated",
            data={"decision_type": "execute", "capability": "inspect_workload"},
            occurred_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
        ),
        event(
            "event-4",
            "ModelUsageRecorded",
            data={
                "provider": "scripted",
                "model": "fixture-model",
                "input_tokens": 10,
                "output_tokens": 3,
                "estimated_cost_micros": 1,
            },
            occurred_at=datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC),
        ),
        event(
            "event-5",
            "EvaluationCompleted",
            data={"status": "completed", "evaluator": "workload-health"},
            occurred_at=datetime(2026, 1, 1, 0, 0, 4, tzinfo=UTC),
        ),
        event(
            "event-6",
            "GoalCompleted",
            occurred_at=datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC),
        ),
    )

    spans = build_runtime_trace_spans(events)

    assert [span.name for span in spans] == [
        "runtime.session",
        "runtime.decision",
        "runtime.decision.validation",
        "runtime.model_usage",
        "runtime.evaluation",
    ]
    root, decision, validation, model_usage, evaluation = spans
    assert decision.parent_span_id == root.span_id
    assert decision.duration_ms == 0.0
    assert decision.attributes["decision_type"] == "execute"
    assert validation.parent_span_id == root.span_id
    assert validation.status == "ok"
    assert validation.attributes["capability"] == "inspect_workload"
    assert model_usage.parent_span_id == root.span_id
    assert model_usage.attributes["provider"] == "scripted"
    assert model_usage.attributes["input_tokens"] == 10
    assert evaluation.parent_span_id == root.span_id
    assert evaluation.status == "ok"
    assert evaluation.attributes["evaluator"] == "workload-health"


@pytest.mark.behavior
def test_runtime_logs_and_traces_project_rejected_decisions() -> None:
    events = (
        event("event-1", "GoalCreated"),
        event(
            "event-2",
            "DecisionRejected",
            data={
                "decision_type": "execute",
                "capability": "inspect_workload",
                "error_code": "validation_error",
                "validation_stage": "context",
                "rejection_reason": "missing required arguments: name",
            },
        ),
        event("event-3", "GoalFailed", data={"error_code": "validation_error"}),
    )

    logs = build_runtime_logs(events=events)
    spans = build_runtime_trace_spans(events)

    rejected_log = next(log for log in logs if log.event_type == "DecisionRejected")
    rejected_span = next(span for span in spans if span.name == "runtime.decision.rejection")
    assert rejected_log.level == "error"
    assert rejected_log.message == "decision rejected: missing required arguments: name"
    assert rejected_span.status == "error"
    assert rejected_span.attributes["validation_stage"] == "context"


@pytest.mark.unit
def test_runtime_trace_spans_project_resource_lock_phases() -> None:
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
                "capability": "scale_workload",
                "tool_name": "kubernetes_scale_workload",
                "resource_key": "deployment/example",
            },
            occurred_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        ),
        event(
            "event-3",
            "ResourceLockAcquired",
            action_id="action-1",
            data={"resource_key": "deployment/example", "resource_version": "rv-1"},
            occurred_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC),
        ),
        event(
            "event-4",
            "ResourceConflictDetected",
            action_id="action-1",
            data={"resource_key": "deployment/example", "reason": "already locked"},
            occurred_at=datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC),
        ),
        event(
            "event-5",
            "GoalFailed",
            occurred_at=datetime(2026, 1, 1, 0, 0, 4, tzinfo=UTC),
        ),
    )

    spans = build_runtime_trace_spans(events)

    assert [span.name for span in spans] == [
        "runtime.session",
        "runtime.action.scale_workload",
        "runtime.resource_lock",
        "runtime.resource_conflict",
    ]
    _, action, lock, conflict = spans
    assert action.status == "error"
    assert action.attributes["resource_key"] == "deployment/example"
    assert lock.parent_span_id == action.span_id
    assert lock.status == "ok"
    assert lock.attributes["resource_version"] == "rv-1"
    assert conflict.parent_span_id == action.span_id
    assert conflict.status == "error"
    assert conflict.attributes["reason"] == "already locked"


@pytest.mark.contract
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


@pytest.mark.unit
def test_opentelemetry_trace_export_keeps_empty_span_list() -> None:
    payload = build_opentelemetry_trace_export(())

    resource_spans = payload["resourceSpans"]
    assert isinstance(resource_spans, list)
    resource_span = resource_spans[0]
    assert isinstance(resource_span, dict)
    scope_spans = resource_span["scopeSpans"]
    assert isinstance(scope_spans, list)
    scope_span = scope_spans[0]
    assert isinstance(scope_span, dict)
    assert scope_span["spans"] == []


@pytest.mark.behavior
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
        "runtime_config",
        "runtime_paths",
        "runtime_secrets",
        "secret_scanning",
        "session_store",
        "event_stream",
        "state_event_commit",
        "state_event_consistency",
        "structured_logs",
        "traces",
        "audit",
        "policy_denials",
        "recovery",
        "resource_locks",
        "distributed_runtime",
        "distributed_work_queue",
        "cost_tracking",
    ]
    assert next(check for check in report.checks if check.name == "event_stream").status == "warn"
    assert (
        next(check for check in report.checks if check.name == "state_event_commit").status == "ok"
    )
    assert (
        next(check for check in report.checks if check.name == "state_event_consistency").status
        == "warn"
    )
    assert next(check for check in report.checks if check.name == "traces").status == "warn"
    assert next(check for check in report.checks if check.name == "resource_locks").status == "ok"
    assert next(check for check in report.checks if check.name == "runtime_config").status == "ok"
    assert next(check for check in report.checks if check.name == "runtime_paths").status == "ok"
    assert next(check for check in report.checks if check.name == "runtime_secrets").status == "ok"
    assert next(check for check in report.checks if check.name == "secret_scanning").status == "ok"


@pytest.mark.unit
def test_doctor_report_errors_on_missing_required_runtime_secrets() -> None:
    report = build_doctor_report(
        health_status="ok",
        ready=False,
        ready_reason="missing required secrets: openai_api_key",
        domain_count=1,
        capability_count=2,
        tool_count=2,
        sessions=(),
        events=(),
        secret_resolution=SecretResolutionReport(
            (
                SecretResolution(
                    "openai_api_key",
                    "env",
                    "OPENAI_API_KEY",
                    True,
                    SecretResolutionStatus.MISSING_REQUIRED,
                ),
            )
        ),
    )

    runtime_secrets = next(check for check in report.checks if check.name == "runtime_secrets")

    assert report.status == "error"
    assert runtime_secrets.status == "error"
    assert "openai_api_key" in runtime_secrets.message


@pytest.mark.contract
def test_doctor_report_errors_on_unredacted_secret_payloads() -> None:
    report = build_doctor_report(
        health_status="ok",
        ready=True,
        ready_reason="ready",
        domain_count=1,
        capability_count=2,
        tool_count=2,
        sessions=(),
        events=(),
        secret_scan_payload={"events": [{"api_token": "secret-token"}]},
    )

    secret_scanning = next(check for check in report.checks if check.name == "secret_scanning")

    assert report.status == "error"
    assert secret_scanning.status == "error"
    assert "$.events[0].api_token" in secret_scanning.message


@pytest.mark.behavior
def test_doctor_report_errors_on_state_event_consistency_gaps() -> None:
    report = build_doctor_report(
        health_status="ok",
        ready=True,
        ready_reason="ready",
        domain_count=1,
        capability_count=2,
        tool_count=2,
        sessions=(session(GoalStatus.COMPLETED),),
        events=(
            event("event-1", "GoalCreated"),
            event("event-2", "GoalCreated", session_id="orphan-session"),
        ),
    )

    consistency = next(check for check in report.checks if check.name == "state_event_consistency")

    assert report.status == "error"
    assert consistency.status == "error"
    assert "orphan_events=1" in consistency.message
    assert "terminal_event_gaps=1" in consistency.message


@pytest.mark.unit
def test_doctor_report_accepts_consistent_terminal_session_events() -> None:
    report = build_doctor_report(
        health_status="ok",
        ready=True,
        ready_reason="ready",
        domain_count=1,
        capability_count=2,
        tool_count=2,
        sessions=(session(GoalStatus.COMPLETED),),
        events=(event("event-1", "GoalCreated"), event("event-2", "GoalCompleted")),
    )

    consistency = next(check for check in report.checks if check.name == "state_event_consistency")

    assert consistency.status == "ok"
    assert "terminal_events_verified" in consistency.message


@pytest.mark.unit
def test_doctor_report_errors_when_persistent_state_event_commit_is_split() -> None:
    report = build_doctor_report(
        health_status="ok",
        ready=True,
        ready_reason="ready",
        domain_count=1,
        capability_count=2,
        tool_count=2,
        sessions=(),
        events=(),
        store_backend="sqlite",
        state_event_commit_supported=False,
        state_event_commit_strategy="split_store",
        state_event_commit_shared_store=False,
    )

    commit = next(check for check in report.checks if check.name == "state_event_commit")

    assert report.status == "error"
    assert commit.status == "error"
    assert "persistent store lacks state/event commit support" in commit.message


@pytest.mark.unit
def test_doctor_report_accepts_persistent_state_event_commit_strategy() -> None:
    report = build_doctor_report(
        health_status="ok",
        ready=True,
        ready_reason="ready",
        domain_count=1,
        capability_count=2,
        tool_count=2,
        sessions=(),
        events=(),
        store_backend="file",
        state_event_commit_supported=True,
        state_event_commit_strategy="file_journal",
        state_event_commit_shared_store=True,
    )

    commit = next(check for check in report.checks if check.name == "state_event_commit")

    assert commit.status == "ok"
    assert "file_journal" in commit.message


@pytest.mark.contract
def test_doctor_report_errors_on_invalid_runtime_config_projection() -> None:
    report = build_doctor_report(
        health_status="ok",
        ready=True,
        ready_reason="ready",
        domain_count=1,
        capability_count=2,
        tool_count=2,
        sessions=(),
        events=(),
        configured_domain_count=2,
        store_backend="unsupported",
        max_iterations=0,
        max_recovery_steps=0,
    )

    runtime_config = next(check for check in report.checks if check.name == "runtime_config")

    assert report.status == "error"
    assert runtime_config.status == "error"
    assert "configured_domains=2 active_domains=1" in runtime_config.message
    assert "unsupported store backend: unsupported" in runtime_config.message
    assert "max_iterations=0" in runtime_config.message
    assert "max_recovery_steps=0" in runtime_config.message


@pytest.mark.unit
def test_doctor_report_accepts_ready_persistent_runtime_paths(tmp_path: Path) -> None:
    store_path = tmp_path / "runtime-store"
    store_path.mkdir()

    report = build_doctor_report(
        health_status="ok",
        ready=True,
        ready_reason="ready",
        domain_count=1,
        capability_count=2,
        tool_count=2,
        sessions=(),
        events=(),
        store_backend="file",
        store_path=str(store_path),
        distributed_queue_backend="file",
        distributed_queue_path=str(tmp_path / "work-queue.json"),
        distributed_locks_backend="sqlite",
        distributed_locks_path=str(tmp_path / "distributed-locks.sqlite3"),
        distributed_workers_backend="memory",
    )

    runtime_paths = next(check for check in report.checks if check.name == "runtime_paths")

    assert report.status == "ok"
    assert runtime_paths.status == "ok"
    assert runtime_paths.message == "persistent runtime paths ready: 3"


@pytest.mark.unit
def test_doctor_report_warns_on_runtime_paths_that_will_be_created(tmp_path: Path) -> None:
    report = build_doctor_report(
        health_status="ok",
        ready=True,
        ready_reason="ready",
        domain_count=1,
        capability_count=2,
        tool_count=2,
        sessions=(),
        events=(),
        store_backend="file",
        store_path=str(tmp_path / "runtime-store"),
        distributed_queue_backend="file",
        distributed_queue_path=str(tmp_path / "missing" / "work-queue.json"),
    )

    runtime_paths = next(check for check in report.checks if check.name == "runtime_paths")

    assert report.status == "warn"
    assert runtime_paths.status == "warn"
    assert f"runtime_store directory will be created: {tmp_path / 'runtime-store'}" in (
        runtime_paths.message
    )
    assert (
        f"distributed_queue parent directory will be created: {tmp_path / 'missing'}"
        in runtime_paths.message
    )


@pytest.mark.unit
def test_doctor_report_errors_on_invalid_runtime_path_shapes(tmp_path: Path) -> None:
    store_path = tmp_path / "runtime-store"
    queue_path = tmp_path / "work-queue.json"
    store_path.write_text("not a directory", encoding="utf-8")
    queue_path.mkdir()

    report = build_doctor_report(
        health_status="ok",
        ready=True,
        ready_reason="ready",
        domain_count=1,
        capability_count=2,
        tool_count=2,
        sessions=(),
        events=(),
        store_backend="file",
        store_path=str(store_path),
        distributed_queue_backend="file",
        distributed_queue_path=str(queue_path),
        distributed_locks_backend="sqlite",
    )

    runtime_paths = next(check for check in report.checks if check.name == "runtime_paths")

    assert report.status == "error"
    assert runtime_paths.status == "error"
    assert f"runtime_store expected directory but path is file: {store_path}" in (
        runtime_paths.message
    )
    assert f"distributed_queue expected file but path is directory: {queue_path}" in (
        runtime_paths.message
    )
    assert "distributed_locks sqlite backend requires path" in runtime_paths.message


@pytest.mark.unit
def test_doctor_report_warns_on_resource_lock_conflicts() -> None:
    report = build_doctor_report(
        health_status="ok",
        ready=True,
        ready_reason="ready",
        domain_count=1,
        capability_count=2,
        tool_count=2,
        sessions=(session(GoalStatus.WAITING, pending_action=True),),
        events=(
            event(
                "event-1",
                "ResourceLockAcquired",
                action_id="action-1",
                data={"resource_key": "deployment/example"},
            ),
            event(
                "event-2",
                "ResourceConflictDetected",
                action_id="action-2",
                data={"resource_key": "deployment/example"},
            ),
        ),
    )

    resource_locks = next(check for check in report.checks if check.name == "resource_locks")

    assert report.status == "warn"
    assert resource_locks.status == "warn"
    assert resource_locks.message == "resource conflicts detected: 1"


@pytest.mark.behavior
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
        event(
            "event-4",
            "PolicyChecked",
            action_id="action-3",
            data={
                "effect": "allow",
                "policy": "kubernetes-scale-safety",
                "capability": "scale_workload",
                "tool_name": "kubernetes_scale_workload",
                "side_effect": "reversible",
                "risk": "medium",
            },
            occurred_at=datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
        ),
        event(
            "event-5",
            "ResourceConflictDetected",
            action_id="action-3",
            data={"resource_key": "deployment/example"},
            occurred_at=datetime(2026, 1, 1, 0, 3, tzinfo=UTC),
        ),
    )

    records = build_audit_records(events)

    assert len(records) == 2
    assert records[0].record_id == "event-4"
    assert records[0].status == "resource_conflict"
    assert records[0].error_code is ErrorCode.RESOURCE_CONFLICT
    assert records[0].completed_at == datetime(2026, 1, 1, 0, 3, tzinfo=UTC)
    assert records[1].record_id == "event-1"
    assert records[1].capability == "scale_workload"
    assert records[1].policy_effect == "require_confirmation"
    assert records[1].status == "confirmation_required"
    assert records[1].completed_at == datetime(2026, 1, 1, 0, 1, tzinfo=UTC)


@pytest.mark.unit
def test_doctor_report_includes_distributed_runtime_health() -> None:
    report = build_doctor_report(
        health_status="ok",
        ready=True,
        ready_reason="ready",
        domain_count=1,
        capability_count=1,
        tool_count=1,
        sessions=(),
        events=(),
        distributed_health_status="warn",
        distributed_health_check_count=6,
        distributed_capacity_gap_count=1,
        distributed_expiring_lease_count=2,
        distributed_recommendation_count=3,
    )

    distributed = next(check for check in report.checks if check.name == "distributed_runtime")

    assert report.status == "warn"
    assert distributed.status == "warn"
    assert distributed.message == (
        "status=warn checks=6 capacity_gaps=1 expiring_leases=2 recommendations=3"
    )


@pytest.mark.unit
def test_doctor_report_errors_on_invalid_distributed_session_work_items() -> None:
    report = build_doctor_report(
        health_status="ok",
        ready=True,
        ready_reason="ready",
        domain_count=1,
        capability_count=1,
        tool_count=1,
        sessions=(),
        events=(),
        distributed_invalid_session_work_item_count=2,
    )

    distributed_queue = next(
        check for check in report.checks if check.name == "distributed_work_queue"
    )

    assert report.status == "error"
    assert distributed_queue.status == "error"
    assert distributed_queue.message == "invalid_session_work_items=2"


@pytest.mark.unit
def test_doctor_report_warns_on_terminal_distributed_work_items() -> None:
    report = build_doctor_report(
        health_status="ok",
        ready=True,
        ready_reason="ready",
        domain_count=1,
        capability_count=1,
        tool_count=1,
        sessions=(),
        events=(),
        distributed_invalid_session_work_item_count=0,
        distributed_terminal_work_item_count=3,
    )

    distributed_queue = next(
        check for check in report.checks if check.name == "distributed_work_queue"
    )

    assert report.status == "warn"
    assert distributed_queue.status == "warn"
    assert distributed_queue.message == "terminal_work_items=3 prune recommended"


@pytest.mark.unit
def test_doctor_report_allows_missing_distributed_runtime() -> None:
    report = build_doctor_report(
        health_status="ok",
        ready=True,
        ready_reason="ready",
        domain_count=1,
        capability_count=1,
        tool_count=1,
        sessions=(),
        events=(),
    )

    distributed = next(check for check in report.checks if check.name == "distributed_runtime")

    assert report.status == "ok"
    assert distributed.status == "ok"
    assert distributed.message == "distributed runtime coordinator not configured"
    queue = next(check for check in report.checks if check.name == "distributed_work_queue")
    assert queue.status == "ok"
    assert queue.message == "distributed work queue not inspected"
