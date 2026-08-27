from __future__ import annotations

from universal_agent.core import ActionId, JsonMapping, JsonValue, SessionId, immutable_json
from universal_agent.operations.helpers import duration_ms, redacted_value, string
from universal_agent.operations.views import RuntimeTraceSpanView
from universal_agent.runtime import RuntimeEventView


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
        duration_ms=duration_ms(first.occurred_at, last.occurred_at),
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
    capability = string(attributes.get("capability")) or "unknown"
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
        duration_ms=duration_ms(first.occurred_at, last.occurred_at),
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
    if event_type == "DecisionValidated":
        return "runtime.decision.validation"
    if event_type == "DecisionRejected":
        return "runtime.decision.rejection"
    if event_type == "ModelUsageRecorded":
        return "runtime.model_usage"
    if event_type == "PolicyChecked":
        return "runtime.policy"
    if event_type == "ObservationReceived":
        return "runtime.observation"
    if event_type == "EvaluationCompleted":
        return "runtime.evaluation"
    if event_type in {"ResourceLockAcquired", "ResourceLockReleased"}:
        return "runtime.resource_lock"
    if event_type == "ResourceConflictDetected":
        return "runtime.resource_conflict"
    return None


def _phase_span_status(event: RuntimeEventView) -> str:
    if event.type == "DecisionRejected":
        return "error"
    if event.type == "ResourceConflictDetected":
        return "error"
    if event.type == "PolicyChecked" and string(event.data.get("effect")) == "deny":
        return "error"
    if event.type == "EvaluationCompleted":
        status = string(event.data.get("status"))
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
            values[str(key)] = redacted_value(str(key), value)
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
    if any(event.type == "ResourceConflictDetected" for event in events):
        return "error"
    for event in events:
        if event.type == "PolicyChecked" and string(event.data.get("effect")) == "deny":
            return "error"
    for event in reversed(events):
        if event.type == "ActionCompleted":
            status = string(event.data.get("status"))
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
            "resource_key",
            "resource_version",
            "arguments",
            "reason",
        ):
            value = event.data.get(key)
            if value is not None and key not in values:
                values[key] = redacted_value(key, value)
    return immutable_json(values)
