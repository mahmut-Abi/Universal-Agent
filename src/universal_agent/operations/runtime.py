from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from universal_agent.core import (
    ActionId,
    ErrorCode,
    GoalId,
    GoalStatus,
    JsonMapping,
    JsonValue,
    SessionId,
    TaskId,
    immutable_json,
)
from universal_agent.runtime import RuntimeEventView, SessionSummaryView


@dataclass(frozen=True, slots=True)
class RuntimeMetricsView:
    session_count: int
    active_session_count: int
    waiting_session_count: int
    completed_goal_count: int
    failed_goal_count: int
    cancelled_goal_count: int
    event_count: int
    action_started_count: int
    action_completed_count: int
    tool_failure_count: int
    policy_denial_count: int
    confirmation_required_count: int
    recovery_planned_count: int
    recovery_exhausted_count: int
    human_intervention_count: int
    model_call_count: int = 0
    model_input_token_count: int = 0
    model_output_token_count: int = 0
    model_total_token_count: int = 0
    model_estimated_cost_micros: int = 0


@dataclass(frozen=True, slots=True)
class ModelCostBreakdownView:
    provider: str
    model: str
    call_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_micros: int
    currency: str


@dataclass(frozen=True, slots=True)
class RuntimeCostView:
    model_call_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_micros: int
    currency: str
    by_model: tuple[ModelCostBreakdownView, ...]


@dataclass(frozen=True, slots=True)
class DoctorCheckView:
    name: str
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class DoctorReportView:
    status: str
    checks: tuple[DoctorCheckView, ...]


@dataclass(frozen=True, slots=True)
class AuditRecordView:
    record_id: str
    session_id: SessionId
    goal_id: GoalId
    task_id: TaskId
    action_id: ActionId | None
    capability: str
    tool_name: str
    side_effect: str
    risk: str
    policy_effect: str
    policy_name: str
    status: str
    occurred_at: datetime
    completed_at: datetime | None = None
    error_code: ErrorCode | None = None


@dataclass(frozen=True, slots=True)
class RuntimeLogRecordView:
    log_id: str
    level: str
    message: str
    event_type: str
    session_id: SessionId
    goal_id: GoalId
    task_id: TaskId
    action_id: ActionId | None
    data: JsonMapping
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RuntimeTraceSpanView:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: str
    status: str
    session_id: SessionId
    goal_id: GoalId
    task_id: TaskId
    action_id: ActionId | None
    start_time: datetime
    end_time: datetime
    duration_ms: float
    attributes: JsonMapping


def build_runtime_metrics(
    sessions: tuple[SessionSummaryView, ...],
    events: tuple[RuntimeEventView, ...],
) -> RuntimeMetricsView:
    cost = build_runtime_cost(events)
    return RuntimeMetricsView(
        session_count=len(sessions),
        active_session_count=sum(
            1
            for session in sessions
            if session.goal_status in {GoalStatus.PENDING, GoalStatus.RUNNING}
        ),
        waiting_session_count=sum(
            1 for session in sessions if session.goal_status is GoalStatus.WAITING
        ),
        completed_goal_count=sum(
            1 for session in sessions if session.goal_status is GoalStatus.COMPLETED
        ),
        failed_goal_count=sum(
            1 for session in sessions if session.goal_status is GoalStatus.FAILED
        ),
        cancelled_goal_count=sum(
            1 for session in sessions if session.goal_status is GoalStatus.CANCELLED
        ),
        event_count=len(events),
        action_started_count=_count(events, "ActionStarted"),
        action_completed_count=_count(events, "ActionCompleted"),
        tool_failure_count=sum(
            1
            for event in events
            if event.type == "ActionCompleted" and _string(event.data.get("status")) != "succeeded"
        ),
        policy_denial_count=sum(
            1
            for event in events
            if event.type == "PolicyChecked" and _string(event.data.get("effect")) == "deny"
        ),
        confirmation_required_count=_count(events, "ConfirmationRequired"),
        recovery_planned_count=_count(events, "RecoveryPlanned"),
        recovery_exhausted_count=_count(events, "RecoveryExhausted"),
        human_intervention_count=sum(
            1 for event in events if event.type in {"ConfirmationRequired", "GoalWaiting"}
        ),
        model_call_count=cost.model_call_count,
        model_input_token_count=cost.input_tokens,
        model_output_token_count=cost.output_tokens,
        model_total_token_count=cost.total_tokens,
        model_estimated_cost_micros=cost.estimated_cost_micros,
    )


def build_prometheus_metrics_export(
    metrics: RuntimeMetricsView,
    *,
    prefix: str = "universal_agent_runtime",
) -> str:
    """Project runtime metrics into Prometheus text exposition format.

    This is a product adapter over RuntimeMetricsView. It does not add a metrics
    backend dependency or change how runtime metrics are derived from events.
    """
    metric_values = (
        ("sessions", "Runtime sessions listed by the session store", metrics.session_count),
        ("active_sessions", "Runtime sessions currently active", metrics.active_session_count),
        (
            "waiting_sessions",
            "Runtime sessions waiting for user/runtime input",
            metrics.waiting_session_count,
        ),
        ("completed_goals", "Runtime goals completed", metrics.completed_goal_count),
        ("failed_goals", "Runtime goals failed", metrics.failed_goal_count),
        ("cancelled_goals", "Runtime goals cancelled", metrics.cancelled_goal_count),
        ("events", "Runtime events recorded", metrics.event_count),
        ("actions_started", "Runtime actions started", metrics.action_started_count),
        ("actions_completed", "Runtime actions completed", metrics.action_completed_count),
        ("tool_failures", "Runtime tool failures observed", metrics.tool_failure_count),
        ("policy_denials", "Runtime policy denials observed", metrics.policy_denial_count),
        (
            "confirmations_required",
            "Runtime confirmations required",
            metrics.confirmation_required_count,
        ),
        ("recoveries_planned", "Runtime recovery plans created", metrics.recovery_planned_count),
        (
            "recoveries_exhausted",
            "Runtime recovery paths exhausted",
            metrics.recovery_exhausted_count,
        ),
        (
            "human_interventions",
            "Runtime human interventions required",
            metrics.human_intervention_count,
        ),
        ("model_calls", "Runtime model calls recorded", metrics.model_call_count),
        (
            "model_input_tokens",
            "Runtime model input tokens recorded",
            metrics.model_input_token_count,
        ),
        (
            "model_output_tokens",
            "Runtime model output tokens recorded",
            metrics.model_output_token_count,
        ),
        (
            "model_total_tokens",
            "Runtime model total tokens recorded",
            metrics.model_total_token_count,
        ),
        (
            "model_estimated_cost_micros",
            "Runtime model estimated cost in micros",
            metrics.model_estimated_cost_micros,
        ),
    )
    lines: list[str] = []
    for suffix, help_text, value in metric_values:
        name = _prometheus_metric_name(prefix, suffix)
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"


def _prometheus_metric_name(prefix: str, suffix: str) -> str:
    name = f"{_prometheus_name_fragment(prefix)}_{_prometheus_name_fragment(suffix)}"
    if name[0].isdigit():
        return f"_{name}"
    return name


def _prometheus_name_fragment(value: str) -> str:
    cleaned = "".join(
        character if character.isascii() and (character.isalnum() or character == "_") else "_"
        for character in value.strip()
    ).strip("_")
    return cleaned or "metric"


def build_runtime_cost(events: tuple[RuntimeEventView, ...]) -> RuntimeCostView:
    accumulators: dict[tuple[str, str, str], _CostAccumulator] = {}
    for event in events:
        if event.type != "ModelUsageRecorded":
            continue
        provider = _string(event.data.get("provider")) or "unknown"
        model = _string(event.data.get("model")) or "unknown"
        currency = _string(event.data.get("currency")) or "USD"
        key = (provider, model, currency)
        if key not in accumulators:
            accumulators[key] = _CostAccumulator(provider, model, currency)
        accumulators[key].add(
            input_tokens=_non_negative_int(event.data.get("input_tokens")),
            output_tokens=_non_negative_int(event.data.get("output_tokens")),
            estimated_cost_micros=_non_negative_int(event.data.get("estimated_cost_micros")),
        )
    by_model = tuple(
        item.view()
        for item in sorted(
            accumulators.values(),
            key=lambda item: (item.provider, item.model, item.currency),
        )
    )
    return RuntimeCostView(
        model_call_count=sum(item.call_count for item in by_model),
        input_tokens=sum(item.input_tokens for item in by_model),
        output_tokens=sum(item.output_tokens for item in by_model),
        total_tokens=sum(item.total_tokens for item in by_model),
        estimated_cost_micros=sum(item.estimated_cost_micros for item in by_model),
        currency=_aggregate_currency(tuple(item.currency for item in by_model)),
        by_model=by_model,
    )


def build_doctor_report(
    *,
    health_status: str,
    ready: bool,
    ready_reason: str,
    domain_count: int,
    capability_count: int,
    tool_count: int,
    sessions: tuple[SessionSummaryView, ...],
    events: tuple[RuntimeEventView, ...],
) -> DoctorReportView:
    metrics = build_runtime_metrics(sessions, events)
    cost = build_runtime_cost(events)
    logs = build_runtime_logs(events)
    trace_spans = build_runtime_trace_spans(events)
    audit_records = build_audit_records(events)
    checks = (
        _check(
            "service_health",
            health_status == "ok",
            f"health status is {health_status}",
        ),
        _check("readiness", ready, ready_reason),
        _check(
            "catalog",
            domain_count > 0 and capability_count > 0 and tool_count > 0,
            f"domains={domain_count} capabilities={capability_count} tools={tool_count}",
        ),
        DoctorCheckView("session_store", "ok", f"sessions listed: {len(sessions)}"),
        _event_stream_check(sessions, events),
        DoctorCheckView("structured_logs", "ok", f"log records projected: {len(logs)}"),
        _trace_projection_check(sessions, events, trace_spans),
        _audit_projection_check(events, audit_records),
        _policy_denial_check(metrics.policy_denial_count),
        _recovery_check(metrics.recovery_exhausted_count),
        DoctorCheckView(
            "cost_tracking",
            "ok",
            "model_calls="
            f"{cost.model_call_count} tokens={cost.total_tokens} "
            f"cost_micros={cost.estimated_cost_micros} currency={cost.currency}",
        ),
    )
    return DoctorReportView(_aggregate_status(checks), checks)


def build_audit_records(
    events: tuple[RuntimeEventView, ...],
    *,
    session_id: SessionId | None = None,
) -> tuple[AuditRecordView, ...]:
    scoped = tuple(
        event for event in events if session_id is None or event.session_id == session_id
    )
    completed = {
        event.action_id: event
        for event in scoped
        if event.type == "ActionCompleted" and event.action_id is not None
    }
    records = tuple(
        _audit_record(
            event,
            None if event.action_id is None else completed.get(event.action_id),
        )
        for event in scoped
        if event.type == "PolicyChecked" and _string(event.data.get("side_effect")) != "none"
    )
    return tuple(sorted(records, key=lambda record: record.occurred_at, reverse=True))


def build_runtime_logs(
    events: tuple[RuntimeEventView, ...],
    *,
    session_id: SessionId | None = None,
) -> tuple[RuntimeLogRecordView, ...]:
    scoped = tuple(
        event for event in events if session_id is None or event.session_id == session_id
    )
    return tuple(
        RuntimeLogRecordView(
            log_id=event.event_id,
            level=_log_level(event),
            message=_log_message(event),
            event_type=event.type,
            session_id=event.session_id,
            goal_id=event.goal_id,
            task_id=event.task_id,
            action_id=event.action_id,
            data=_redacted_mapping(event.data),
            occurred_at=event.occurred_at,
        )
        for event in sorted(scoped, key=lambda item: item.occurred_at)
    )


def build_runtime_trace_spans(
    events: tuple[RuntimeEventView, ...],
    *,
    session_id: SessionId | None = None,
) -> tuple[RuntimeTraceSpanView, ...]:
    """Project runtime events into OpenTelemetry-shaped spans.

    This is an adapter over the existing Event Stream, not a second tracing
    system. It keeps the Runtime authoritative for event generation while
    giving agentd/CLI consumers a stable trace view.
    """
    scoped = tuple(
        sorted(
            (event for event in events if session_id is None or event.session_id == session_id),
            key=lambda event: event.occurred_at,
        )
    )
    spans: list[RuntimeTraceSpanView] = []
    for session_events in _events_by_session(scoped):
        spans.extend(_session_trace_spans(session_events))
    return tuple(spans)


def build_opentelemetry_trace_export(
    spans: tuple[RuntimeTraceSpanView, ...],
    *,
    service_name: str = "universal-agent-runtime",
    scope_name: str = "universal-agent-runtime",
    scope_version: str = "0.1.0",
) -> JsonMapping:
    """Project runtime trace spans into an OTLP JSON-compatible payload.

    The Runtime remains the source of trace semantics. This function is a
    product adapter for collectors and tests: it derives stable hex trace/span
    IDs from runtime IDs, carries redacted attributes forward, and avoids
    adding an OpenTelemetry dependency to the Kernel.
    """
    return immutable_json(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "service.name",
                                "value": {"stringValue": service_name},
                            }
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": scope_name, "version": scope_version},
                            "spans": [_otlp_span(span) for span in spans],
                        }
                    ],
                }
            ]
        }
    )


def _audit_record(
    event: RuntimeEventView,
    completed: RuntimeEventView | None,
) -> AuditRecordView:
    effect = _string(event.data.get("effect"))
    completed_status = _string(completed.data.get("status")) if completed is not None else ""
    error_code = _error_code(completed.data.get("error_code")) if completed is not None else None
    return AuditRecordView(
        record_id=event.event_id,
        session_id=event.session_id,
        goal_id=event.goal_id,
        task_id=event.task_id,
        action_id=event.action_id,
        capability=_string(event.data.get("capability")),
        tool_name=_string(event.data.get("tool_name")),
        side_effect=_string(event.data.get("side_effect")),
        risk=_string(event.data.get("risk")),
        policy_effect=effect,
        policy_name=_string(event.data.get("policy")),
        status=_audit_status(effect, completed_status),
        occurred_at=event.occurred_at,
        completed_at=None if completed is None else completed.occurred_at,
        error_code=error_code,
    )


def _audit_status(policy_effect: str, completed_status: str) -> str:
    if policy_effect == "deny":
        return "denied"
    if policy_effect == "require_confirmation":
        return "confirmation_required"
    if completed_status:
        return completed_status
    return "allowed"


def _log_level(event: RuntimeEventView) -> str:
    if event.type == "PolicyChecked" and _string(event.data.get("effect")) == "deny":
        return "error"
    if (
        event.type == "ActionCompleted"
        and _string(event.data.get("status"))
        and _string(event.data.get("status")) != "succeeded"
    ):
        return "error"
    if event.type in {"GoalFailed", "RecoveryExhausted"}:
        return "error"
    if event.type in {"ConfirmationRequired", "GoalWaiting", "SessionPaused"}:
        return "warn"
    if event.type == "PolicyChecked" and _string(event.data.get("effect")):
        effect = _string(event.data.get("effect"))
        if effect != "allow":
            return "warn"
    if event.type == "RecoveryPlanned":
        return "warn"
    return "info"


def _log_message(event: RuntimeEventView) -> str:
    if event.type == "DecisionGenerated":
        return f"decision generated: {_string(event.data.get('decision_type')) or 'unknown'}"
    if event.type == "PolicyChecked":
        return f"policy checked: {_string(event.data.get('effect')) or 'unknown'}"
    if event.type == "ActionStarted":
        capability = _string(event.data.get("capability")) or "unknown capability"
        tool_name = _string(event.data.get("tool_name")) or "unknown tool"
        return f"action started: {capability} via {tool_name}"
    if event.type == "ActionCompleted":
        return f"action completed: {_string(event.data.get('status')) or 'unknown'}"
    if event.type == "ModelUsageRecorded":
        provider = _string(event.data.get("provider")) or "unknown"
        model = _string(event.data.get("model")) or "unknown"
        return f"model usage recorded: {provider}/{model}"
    if event.type == "EvidenceRecorded":
        return f"evidence recorded: {_string(event.data.get('claim')) or 'unknown claim'}"
    if event.type == "EvaluationCompleted":
        return f"evaluation completed: {_string(event.data.get('status')) or 'unknown'}"
    return _event_words(event.type)


def _session_trace_spans(events: tuple[RuntimeEventView, ...]) -> tuple[RuntimeTraceSpanView, ...]:
    if not events:
        return ()
    first = events[0]
    last = events[-1]
    trace_id = f"trace:{first.session_id}"
    root_span_id = f"span:session:{first.session_id}"
    root = RuntimeTraceSpanView(
        trace_id=trace_id,
        span_id=root_span_id,
        parent_span_id=None,
        name="runtime.session",
        kind="internal",
        status=_session_span_status(events),
        session_id=first.session_id,
        goal_id=first.goal_id,
        task_id=first.task_id,
        action_id=None,
        start_time=first.occurred_at,
        end_time=last.occurred_at,
        duration_ms=_duration_ms(first.occurred_at, last.occurred_at),
        attributes=immutable_json(
            {
                "event_count": len(events),
                "first_event": first.type,
                "last_event": last.type,
            }
        ),
    )
    action_spans = tuple(
        _action_trace_span(trace_id, root_span_id, action_events)
        for action_events in _events_by_action(events)
    )
    action_parents = {
        span.action_id: span.span_id for span in action_spans if span.action_id is not None
    }
    phase_spans = tuple(
        span
        for event in events
        if (span := _phase_trace_span(trace_id, root_span_id, action_parents, event)) is not None
    )
    return (root, *action_spans, *phase_spans)


def _action_trace_span(
    trace_id: str,
    parent_span_id: str,
    events: tuple[RuntimeEventView, ...],
) -> RuntimeTraceSpanView:
    first = events[0]
    last = events[-1]
    attributes = _action_span_attributes(events)
    capability = _string(attributes.get("capability")) or "unknown"
    return RuntimeTraceSpanView(
        trace_id=trace_id,
        span_id=f"span:action:{first.action_id}",
        parent_span_id=parent_span_id,
        name=f"runtime.action.{capability}",
        kind="client" if any(event.type == "ActionStarted" for event in events) else "internal",
        status=_action_span_status(events),
        session_id=first.session_id,
        goal_id=first.goal_id,
        task_id=first.task_id,
        action_id=first.action_id,
        start_time=first.occurred_at,
        end_time=last.occurred_at,
        duration_ms=_duration_ms(first.occurred_at, last.occurred_at),
        attributes=attributes,
    )


def _phase_trace_span(
    trace_id: str,
    root_span_id: str,
    action_parents: dict[ActionId, str],
    event: RuntimeEventView,
) -> RuntimeTraceSpanView | None:
    name = _phase_span_name(event.type)
    if name is None:
        return None
    parent_span_id = (
        action_parents.get(event.action_id, root_span_id)
        if event.action_id is not None
        else root_span_id
    )
    return RuntimeTraceSpanView(
        trace_id=trace_id,
        span_id=f"span:event:{event.event_id}",
        parent_span_id=parent_span_id,
        name=name,
        kind="internal",
        status=_phase_span_status(event),
        session_id=event.session_id,
        goal_id=event.goal_id,
        task_id=event.task_id,
        action_id=event.action_id,
        start_time=event.occurred_at,
        end_time=event.occurred_at,
        duration_ms=0.0,
        attributes=_phase_span_attributes(event),
    )


def _phase_span_name(event_type: str) -> str | None:
    if event_type == "DecisionGenerated":
        return "runtime.decision"
    if event_type == "ModelUsageRecorded":
        return "runtime.model_usage"
    if event_type == "PolicyChecked":
        return "runtime.policy"
    if event_type == "ObservationReceived":
        return "runtime.observation"
    if event_type == "EvaluationCompleted":
        return "runtime.evaluation"
    return None


def _phase_span_status(event: RuntimeEventView) -> str:
    if event.type == "PolicyChecked" and _string(event.data.get("effect")) == "deny":
        return "error"
    if event.type == "EvaluationCompleted":
        status = _string(event.data.get("status"))
        if status == "failed":
            return "error"
        if status == "incomplete":
            return "waiting"
    return "ok"


def _phase_span_attributes(event: RuntimeEventView) -> JsonMapping:
    values: dict[str, JsonValue] = {
        "event_id": event.event_id,
        "event_type": event.type,
    }
    for key, value in event.data.items():
        if value is not None:
            values[str(key)] = _redacted_value(str(key), value)
    return immutable_json(values)


def _events_by_session(
    events: tuple[RuntimeEventView, ...],
) -> tuple[tuple[RuntimeEventView, ...], ...]:
    grouped: dict[SessionId, list[RuntimeEventView]] = {}
    order: list[SessionId] = []
    for event in events:
        if event.session_id not in grouped:
            grouped[event.session_id] = []
            order.append(event.session_id)
        grouped[event.session_id].append(event)
    return tuple(tuple(grouped[item]) for item in order)


def _events_by_action(
    events: tuple[RuntimeEventView, ...],
) -> tuple[tuple[RuntimeEventView, ...], ...]:
    grouped: dict[ActionId, list[RuntimeEventView]] = {}
    order: list[ActionId] = []
    for event in events:
        if event.action_id is None:
            continue
        if event.action_id not in grouped:
            grouped[event.action_id] = []
            order.append(event.action_id)
        grouped[event.action_id].append(event)
    return tuple(tuple(grouped[item]) for item in order)


def _session_span_status(events: tuple[RuntimeEventView, ...]) -> str:
    event_types = {event.type for event in events}
    if "GoalFailed" in event_types or "RecoveryExhausted" in event_types:
        return "error"
    if "GoalCompleted" in event_types:
        return "ok"
    if "GoalWaiting" in event_types or "ConfirmationRequired" in event_types:
        return "waiting"
    return "running"


def _action_span_status(events: tuple[RuntimeEventView, ...]) -> str:
    for event in events:
        if event.type == "PolicyChecked" and _string(event.data.get("effect")) == "deny":
            return "error"
    for event in reversed(events):
        if event.type == "ActionCompleted":
            status = _string(event.data.get("status"))
            if status == "succeeded":
                return "ok"
            return "error"
    if any(event.type == "ConfirmationRequired" for event in events):
        return "waiting"
    return "running"


def _action_span_attributes(events: tuple[RuntimeEventView, ...]) -> JsonMapping:
    values: dict[str, JsonValue] = {
        "event_count": len(events),
        "event_types": [event.type for event in events],
    }
    for event in events:
        for key in (
            "capability",
            "tool_name",
            "domain_name",
            "domain_version",
            "effect",
            "policy",
            "side_effect",
            "risk",
            "status",
            "error_code",
            "idempotency_key",
            "parameters_hash",
            "attempt",
            "arguments",
        ):
            value = event.data.get(key)
            if value is not None and key not in values:
                values[key] = _redacted_value(key, value)
    return immutable_json(values)


def _duration_ms(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds() * 1000, 3)


def _event_stream_check(
    sessions: tuple[SessionSummaryView, ...],
    events: tuple[RuntimeEventView, ...],
) -> DoctorCheckView:
    if not sessions:
        return DoctorCheckView("event_stream", "ok", "no sessions recorded")
    if events:
        return DoctorCheckView("event_stream", "ok", f"events listed: {len(events)}")
    return DoctorCheckView("event_stream", "warn", "sessions exist but no events were listed")


def _trace_projection_check(
    sessions: tuple[SessionSummaryView, ...],
    events: tuple[RuntimeEventView, ...],
    spans: tuple[RuntimeTraceSpanView, ...],
) -> DoctorCheckView:
    if not sessions:
        return DoctorCheckView("traces", "ok", "no sessions recorded")
    if events and spans:
        return DoctorCheckView("traces", "ok", f"trace spans projected: {len(spans)}")
    if not events:
        return DoctorCheckView("traces", "warn", "sessions exist but no trace events were listed")
    return DoctorCheckView("traces", "warn", "events exist but no trace spans were projected")


def _audit_projection_check(
    events: tuple[RuntimeEventView, ...],
    audit_records: tuple[AuditRecordView, ...],
) -> DoctorCheckView:
    expected = sum(
        1
        for event in events
        if event.type == "PolicyChecked" and _string(event.data.get("side_effect")) != "none"
    )
    if expected == len(audit_records):
        return DoctorCheckView("audit", "ok", f"audit records projected: {len(audit_records)}")
    return DoctorCheckView(
        "audit",
        "error",
        f"expected {expected} audit records, projected {len(audit_records)}",
    )


def _policy_denial_check(count: int) -> DoctorCheckView:
    if count:
        return DoctorCheckView("policy_denials", "warn", f"policy denials observed: {count}")
    return DoctorCheckView("policy_denials", "ok", "no policy denials observed")


def _recovery_check(count: int) -> DoctorCheckView:
    if count:
        return DoctorCheckView("recovery", "warn", f"exhausted recovery paths: {count}")
    return DoctorCheckView("recovery", "ok", "no exhausted recovery paths observed")


def _check(name: str, ok: bool, message: str) -> DoctorCheckView:
    return DoctorCheckView(name, "ok" if ok else "error", message)


def _aggregate_status(checks: tuple[DoctorCheckView, ...]) -> str:
    if any(check.status == "error" for check in checks):
        return "error"
    if any(check.status == "warn" for check in checks):
        return "warn"
    return "ok"


def _count(events: tuple[RuntimeEventView, ...], event_type: str) -> int:
    return sum(1 for event in events if event.type == event_type)


def _string(value: JsonValue | object) -> str:
    if isinstance(value, str):
        return value
    return ""


def _error_code(value: JsonValue | object) -> ErrorCode | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return ErrorCode(value)
    except ValueError:
        return None


def _non_negative_int(value: JsonValue | object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _aggregate_currency(currencies: tuple[str, ...]) -> str:
    if not currencies:
        return "USD"
    unique = frozenset(currencies)
    if len(unique) == 1:
        return currencies[0]
    return "mixed"


def _otlp_span(span: RuntimeTraceSpanView) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "traceId": _stable_hex(span.trace_id, length=32),
        "spanId": _stable_hex(span.span_id, length=16),
        "name": span.name,
        "kind": _otlp_span_kind(span.kind),
        "startTimeUnixNano": str(_unix_nano(span.start_time)),
        "endTimeUnixNano": str(_unix_nano(span.end_time)),
        "status": {
            "code": _otlp_status_code(span.status),
            "message": span.status,
        },
        "attributes": _otlp_attributes(
            {
                "runtime.session_id": str(span.session_id),
                "runtime.goal_id": str(span.goal_id),
                "runtime.task_id": str(span.task_id),
                "runtime.action_id": None if span.action_id is None else str(span.action_id),
                **dict(span.attributes),
            }
        ),
    }
    if span.parent_span_id is not None:
        payload["parentSpanId"] = _stable_hex(span.parent_span_id, length=16)
    return payload


def _otlp_attributes(values: dict[str, JsonValue]) -> list[JsonValue]:
    return [
        {"key": key, "value": _otlp_any_value(value)}
        for key, value in sorted(values.items())
        if value is not None
    ]


def _otlp_any_value(value: JsonValue) -> dict[str, JsonValue]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [_otlp_any_value(item) for item in value]}}
    if isinstance(value, dict):
        return {
            "kvlistValue": {
                "values": [
                    {"key": str(key), "value": _otlp_any_value(item)}
                    for key, item in sorted(value.items())
                ]
            }
        }
    return {"stringValue": str(value)}


def _otlp_span_kind(kind: str) -> str:
    if kind == "client":
        return "SPAN_KIND_CLIENT"
    if kind == "server":
        return "SPAN_KIND_SERVER"
    return "SPAN_KIND_INTERNAL"


def _otlp_status_code(status: str) -> str:
    if status == "ok":
        return "STATUS_CODE_OK"
    if status == "error":
        return "STATUS_CODE_ERROR"
    return "STATUS_CODE_UNSET"


def _stable_hex(value: str, *, length: int) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _unix_nano(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def _redacted_mapping(values: JsonMapping) -> JsonMapping:
    return immutable_json({key: _redacted_value(key, value) for key, value in values.items()})


def _redacted_value(key: str, value: object) -> JsonValue:
    if _sensitive_key(key):
        return "[REDACTED]"
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, dict):
        return {
            str(item_key): _redacted_value(str(item_key), item) for item_key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redacted_value(key, item) for item in value]
    return str(value)


def _sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    public_token_metrics = {
        "cached_tokens",
        "input_tokens",
        "model_input_tokens",
        "model_output_tokens",
        "model_total_tokens",
        "output_tokens",
        "total_tokens",
    }
    if normalized in public_token_metrics:
        return False
    return any(
        marker in normalized
        for marker in (
            "api_key",
            "authorization",
            "credential",
            "password",
            "secret",
            "token",
        )
    )


def _event_words(event_type: str) -> str:
    words: list[str] = []
    current = ""
    for character in event_type:
        if character.isupper() and current:
            words.append(current.lower())
            current = character
            continue
        current += character
    if current:
        words.append(current.lower())
    return " ".join(words) or event_type


@dataclass(slots=True)
class _CostAccumulator:
    provider: str
    model: str
    currency: str
    call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_micros: int = 0

    def add(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_micros: int,
    ) -> None:
        self.call_count += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.estimated_cost_micros += estimated_cost_micros

    def view(self) -> ModelCostBreakdownView:
        return ModelCostBreakdownView(
            provider=self.provider,
            model=self.model,
            call_count=self.call_count,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_tokens=self.input_tokens + self.output_tokens,
            estimated_cost_micros=self.estimated_cost_micros,
            currency=self.currency,
        )
