from __future__ import annotations

import hashlib

from universal_agent.core import ErrorCode, JsonValue, SessionId, dumps_json
from universal_agent.operations.helpers import (
    error_code,
    event_words,
    redacted_mapping,
    string,
)
from universal_agent.operations.views import (
    AuditIntegrityRecordView,
    AuditIntegrityReportView,
    AuditRecordView,
    RuntimeLogRecordView,
)
from universal_agent.runtime import RuntimeEventView


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
    conflicts = {
        event.action_id: event
        for event in scoped
        if event.type == "ResourceConflictDetected" and event.action_id is not None
    }
    records = tuple(
        _audit_record(
            event,
            None if event.action_id is None else completed.get(event.action_id),
            None if event.action_id is None else conflicts.get(event.action_id),
        )
        for event in scoped
        if event.type == "PolicyChecked" and string(event.data.get("side_effect")) != "none"
    )
    return tuple(sorted(records, key=lambda record: record.occurred_at, reverse=True))


def build_audit_integrity(records: tuple[AuditRecordView, ...]) -> AuditIntegrityReportView:
    previous_hash = _hash_text("audit-chain-v1")
    links: list[AuditIntegrityRecordView] = []
    for record in sorted(records, key=lambda item: (item.occurred_at, item.record_id)):
        record_hash = _hash_mapping(
            {
                "previous_hash": previous_hash,
                "record": _audit_record_hash_payload(record),
            }
        )
        links.append(AuditIntegrityRecordView(record.record_id, previous_hash, record_hash))
        previous_hash = record_hash
    return AuditIntegrityReportView(len(records), previous_hash, tuple(links))


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
            data=redacted_mapping(event.data),
            occurred_at=event.occurred_at,
        )
        for event in sorted(scoped, key=lambda item: item.occurred_at)
    )


def _audit_record_hash_payload(record: AuditRecordView) -> dict[str, JsonValue]:
    return {
        "record_id": record.record_id,
        "session_id": str(record.session_id),
        "goal_id": str(record.goal_id),
        "task_id": str(record.task_id),
        "action_id": None if record.action_id is None else str(record.action_id),
        "capability": record.capability,
        "tool_name": record.tool_name,
        "side_effect": record.side_effect,
        "risk": record.risk,
        "policy_effect": record.policy_effect,
        "policy_name": record.policy_name,
        "status": record.status,
        "occurred_at": record.occurred_at.isoformat(),
        "completed_at": None if record.completed_at is None else record.completed_at.isoformat(),
        "error_code": None if record.error_code is None else record.error_code.value,
    }


def _hash_mapping(value: dict[str, JsonValue]) -> str:
    return _hash_text(dumps_json(value))


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _audit_record(
    event: RuntimeEventView,
    completed: RuntimeEventView | None,
    conflict: RuntimeEventView | None,
) -> AuditRecordView:
    effect = string(event.data.get("effect"))
    completed_status = string(completed.data.get("status")) if completed is not None else ""
    error: ErrorCode | None
    if conflict is not None:
        completed_status = "resource_conflict"
        error = ErrorCode.RESOURCE_CONFLICT
    else:
        error = error_code(completed.data.get("error_code")) if completed is not None else None
    return AuditRecordView(
        record_id=event.event_id,
        session_id=event.session_id,
        goal_id=event.goal_id,
        task_id=event.task_id,
        action_id=event.action_id,
        capability=string(event.data.get("capability")),
        tool_name=string(event.data.get("tool_name")),
        side_effect=string(event.data.get("side_effect")),
        risk=string(event.data.get("risk")),
        policy_effect=effect,
        policy_name=string(event.data.get("policy")),
        status=_audit_status(effect, completed_status),
        occurred_at=event.occurred_at,
        completed_at=(
            conflict.occurred_at
            if conflict is not None
            else None
            if completed is None
            else completed.occurred_at
        ),
        error_code=error,
    )


def _audit_status(policy_effect: str, completed_status: str) -> str:
    if completed_status == "resource_conflict":
        return "resource_conflict"
    if policy_effect == "deny":
        return "denied"
    if policy_effect == "require_confirmation":
        return "confirmation_required"
    if completed_status:
        return completed_status
    return "allowed"


def _log_level(event: RuntimeEventView) -> str:
    if event.type == "DecisionRejected":
        return "error"
    if event.type == "ResourceConflictDetected":
        return "error"
    if event.type == "PolicyChecked" and string(event.data.get("effect")) == "deny":
        return "error"
    if (
        event.type == "ActionCompleted"
        and string(event.data.get("status"))
        and string(event.data.get("status")) != "succeeded"
    ):
        return "error"
    if event.type in {"GoalFailed", "RecoveryExhausted"}:
        return "error"
    if event.type in {"ConfirmationRequired", "GoalWaiting", "SessionPaused"}:
        return "warn"
    if event.type == "PolicyChecked" and string(event.data.get("effect")):
        effect = string(event.data.get("effect"))
        if effect != "allow":
            return "warn"
    if event.type == "RecoveryPlanned":
        return "warn"
    return "info"


def _log_message(event: RuntimeEventView) -> str:
    if event.type == "DecisionGenerated":
        return f"decision generated: {string(event.data.get('decision_type')) or 'unknown'}"
    if event.type == "DecisionValidated":
        return f"decision validated: {string(event.data.get('decision_type')) or 'unknown'}"
    if event.type == "DecisionRejected":
        reason = string(event.data.get("rejection_reason")) or "unknown reason"
        return f"decision rejected: {reason}"
    if event.type == "PolicyChecked":
        return f"policy checked: {string(event.data.get('effect')) or 'unknown'}"
    if event.type == "ActionStarted":
        capability = string(event.data.get("capability")) or "unknown capability"
        tool_name = string(event.data.get("tool_name")) or "unknown tool"
        return f"action started: {capability} via {tool_name}"
    if event.type == "ActionCompleted":
        return f"action completed: {string(event.data.get('status')) or 'unknown'}"
    if event.type == "ModelUsageRecorded":
        provider = string(event.data.get("provider")) or "unknown"
        model = string(event.data.get("model")) or "unknown"
        return f"model usage recorded: {provider}/{model}"
    if event.type == "EvidenceRecorded":
        return f"evidence recorded: {string(event.data.get('claim')) or 'unknown claim'}"
    if event.type == "EvaluationCompleted":
        return f"evaluation completed: {string(event.data.get('status')) or 'unknown'}"
    if event.type == "ResourceLockAcquired":
        return f"resource lock acquired: {string(event.data.get('resource_key')) or 'unknown'}"
    if event.type == "ResourceLockReleased":
        return f"resource lock released: {string(event.data.get('resource_key')) or 'unknown'}"
    if event.type == "ResourceConflictDetected":
        return f"resource conflict detected: {string(event.data.get('resource_key')) or 'unknown'}"
    return event_words(event.type)
