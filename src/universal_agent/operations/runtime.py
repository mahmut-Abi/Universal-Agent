from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from universal_agent.core import (
    ActionId,
    ErrorCode,
    GoalId,
    GoalStatus,
    JsonValue,
    SessionId,
    TaskId,
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


def build_runtime_metrics(
    sessions: tuple[SessionSummaryView, ...],
    events: tuple[RuntimeEventView, ...],
) -> RuntimeMetricsView:
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
        _policy_denial_check(metrics.policy_denial_count),
        _recovery_check(metrics.recovery_exhausted_count),
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


def _event_stream_check(
    sessions: tuple[SessionSummaryView, ...],
    events: tuple[RuntimeEventView, ...],
) -> DoctorCheckView:
    if not sessions:
        return DoctorCheckView("event_stream", "ok", "no sessions recorded")
    if events:
        return DoctorCheckView("event_stream", "ok", f"events listed: {len(events)}")
    return DoctorCheckView("event_stream", "warn", "sessions exist but no events were listed")


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
