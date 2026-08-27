from __future__ import annotations

from universal_agent.core import ActionId, GoalStatus, SessionId
from universal_agent.operations.audit_logs import build_audit_records, build_runtime_logs
from universal_agent.operations.cost import build_runtime_cost
from universal_agent.operations.helpers import string as _string
from universal_agent.operations.traces import build_runtime_trace_spans
from universal_agent.operations.views import (
    AuditRecordView,
    DoctorCheckView,
    DoctorReportView,
    RuntimeMetricsView,
    RuntimeTraceSpanView,
)
from universal_agent.runtime import RuntimeEventView, SessionSummaryView
from universal_agent.security import SecretResolutionReport, scan_for_secrets

_TERMINAL_EVENT_BY_GOAL_STATUS = {
    GoalStatus.COMPLETED: "GoalCompleted",
    GoalStatus.FAILED: "GoalFailed",
    GoalStatus.CANCELLED: "GoalCancelled",
}


def build_runtime_metrics(
    sessions: tuple[SessionSummaryView, ...],
    events: tuple[RuntimeEventView, ...],
) -> RuntimeMetricsView:
    cost = build_runtime_cost(events)
    active_resource_locks = _active_resource_locks(events)
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
        resource_lock_acquired_count=_count(events, "ResourceLockAcquired"),
        resource_lock_released_count=_count(events, "ResourceLockReleased"),
        resource_conflict_count=_count(events, "ResourceConflictDetected"),
        active_resource_lock_count=len(active_resource_locks),
        decision_generated_count=_count(events, "DecisionGenerated"),
        decision_validated_count=_count(events, "DecisionValidated"),
        decision_rejected_count=_count(events, "DecisionRejected"),
        model_call_count=cost.model_call_count,
        model_input_token_count=cost.input_tokens,
        model_output_token_count=cost.output_tokens,
        model_total_token_count=cost.total_tokens,
        model_estimated_cost_micros=cost.estimated_cost_micros,
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
    configured_domain_count: int | None = None,
    store_backend: str | None = None,
    max_iterations: int | None = None,
    max_recovery_steps: int | None = None,
    distributed_health_status: str | None = None,
    distributed_health_check_count: int | None = None,
    distributed_capacity_gap_count: int | None = None,
    distributed_expiring_lease_count: int | None = None,
    distributed_recommendation_count: int | None = None,
    distributed_invalid_session_work_item_count: int | None = None,
    distributed_terminal_work_item_count: int | None = None,
    secret_resolution: SecretResolutionReport | None = None,
    secret_scan_payload: object | None = None,
    state_event_commit_supported: bool | None = None,
    state_event_commit_strategy: str | None = None,
    state_event_commit_shared_store: bool | None = None,
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
        _runtime_config_check(
            domain_count=domain_count,
            configured_domain_count=configured_domain_count,
            store_backend=store_backend,
            max_iterations=max_iterations,
            max_recovery_steps=max_recovery_steps,
        ),
        _runtime_secrets_check(secret_resolution),
        _secret_scanning_check(secret_scan_payload),
        DoctorCheckView("session_store", "ok", f"sessions listed: {len(sessions)}"),
        _event_stream_check(sessions, events),
        _state_event_commit_check(
            store_backend=store_backend,
            supported=state_event_commit_supported,
            strategy=state_event_commit_strategy,
            shared_store=state_event_commit_shared_store,
        ),
        _state_event_consistency_check(sessions, events),
        DoctorCheckView("structured_logs", "ok", f"log records projected: {len(logs)}"),
        _trace_projection_check(sessions, events, trace_spans),
        _audit_projection_check(events, audit_records),
        _policy_denial_check(metrics.policy_denial_count),
        _recovery_check(metrics.recovery_exhausted_count),
        _resource_lock_check(sessions, metrics),
        _distributed_runtime_check(
            status=distributed_health_status,
            check_count=distributed_health_check_count,
            capacity_gap_count=distributed_capacity_gap_count,
            expiring_lease_count=distributed_expiring_lease_count,
            recommendation_count=distributed_recommendation_count,
        ),
        _distributed_work_queue_check(
            invalid_session_work_item_count=distributed_invalid_session_work_item_count,
            terminal_work_item_count=distributed_terminal_work_item_count,
        ),
        DoctorCheckView(
            "cost_tracking",
            "ok",
            "model_calls="
            f"{cost.model_call_count} tokens={cost.total_tokens} "
            f"cost_micros={cost.estimated_cost_micros} currency={cost.currency}",
        ),
    )
    return DoctorReportView(_aggregate_status(checks), checks)


def _runtime_secrets_check(report: SecretResolutionReport | None) -> DoctorCheckView:
    if report is None:
        return DoctorCheckView("runtime_secrets", "ok", "no secret resolution report provided")
    if report.passed:
        available_count = sum(1 for item in report.items if item.available)
        return DoctorCheckView(
            "runtime_secrets",
            "ok",
            f"secret_refs={len(report.items)} available={available_count}",
        )
    sample = ", ".join(report.missing_required_names[:5])
    return DoctorCheckView(
        "runtime_secrets",
        "error",
        f"missing required secret refs: count={len(report.blocking)} names={sample}",
    )


def _secret_scanning_check(payload: object | None) -> DoctorCheckView:
    if payload is None:
        return DoctorCheckView("secret_scanning", "ok", "no secret scan payload provided")
    report = scan_for_secrets(payload)
    if report.passed:
        return DoctorCheckView("secret_scanning", "ok", "no unredacted secret-like values found")
    sample = ", ".join(report.paths[:5])
    return DoctorCheckView(
        "secret_scanning",
        "error",
        f"unredacted secret-like values found: count={len(report.findings)} paths={sample}",
    )


def _event_stream_check(
    sessions: tuple[SessionSummaryView, ...],
    events: tuple[RuntimeEventView, ...],
) -> DoctorCheckView:
    if not sessions:
        return DoctorCheckView("event_stream", "ok", "no sessions recorded")
    if events:
        return DoctorCheckView("event_stream", "ok", f"events listed: {len(events)}")
    return DoctorCheckView("event_stream", "warn", "sessions exist but no events were listed")


def _state_event_consistency_check(
    sessions: tuple[SessionSummaryView, ...],
    events: tuple[RuntimeEventView, ...],
) -> DoctorCheckView:
    if not sessions and not events:
        return DoctorCheckView("state_event_consistency", "ok", "no sessions or events recorded")
    if sessions and not events:
        return DoctorCheckView(
            "state_event_consistency",
            "warn",
            "sessions exist but no events were available for consistency checks",
        )

    session_ids = frozenset(session.session_id for session in sessions)
    orphan_event_count = sum(1 for event in events if event.session_id not in session_ids)
    events_by_session: dict[SessionId, set[str]] = {}
    for event in events:
        events_by_session.setdefault(event.session_id, set()).add(event.type)

    missing_event_history_count = sum(
        1 for session in sessions if session.session_id not in events_by_session
    )
    terminal_event_gap_count = sum(
        1
        for session in sessions
        if (expected := _TERMINAL_EVENT_BY_GOAL_STATUS.get(session.goal_status)) is not None
        and expected not in events_by_session.get(session.session_id, set())
    )
    if orphan_event_count or terminal_event_gap_count:
        return DoctorCheckView(
            "state_event_consistency",
            "error",
            "orphan_events="
            f"{orphan_event_count} terminal_event_gaps={terminal_event_gap_count} "
            f"missing_event_histories={missing_event_history_count}",
        )
    if missing_event_history_count:
        return DoctorCheckView(
            "state_event_consistency",
            "warn",
            f"sessions missing event history: {missing_event_history_count}",
        )
    return DoctorCheckView(
        "state_event_consistency",
        "ok",
        f"sessions={len(sessions)} events={len(events)} terminal_events_verified",
    )


def _state_event_commit_check(
    *,
    store_backend: str | None,
    supported: bool | None,
    strategy: str | None,
    shared_store: bool | None,
) -> DoctorCheckView:
    if supported is None:
        return DoctorCheckView("state_event_commit", "ok", "state/event commit path not inspected")

    backend = store_backend or "unknown"
    detail = f"backend={backend} strategy={strategy or 'unknown'} shared_store={shared_store}"
    if backend == "memory":
        return DoctorCheckView(
            "state_event_commit",
            "ok",
            f"in-memory runtime uses non-durable state/event writes: {detail}",
        )
    if backend in {"file", "sqlite"}:
        if supported and shared_store:
            return DoctorCheckView(
                "state_event_commit",
                "ok",
                f"persistent state/event commit is enabled: {detail}",
            )
        if supported:
            return DoctorCheckView(
                "state_event_commit",
                "error",
                f"persistent state/event committer is split from event reads: {detail}",
            )
        return DoctorCheckView(
            "state_event_commit",
            "error",
            f"persistent store lacks state/event commit support: {detail}",
        )
    if supported and shared_store:
        return DoctorCheckView(
            "state_event_commit",
            "ok",
            f"custom state/event commit path is enabled: {detail}",
        )
    return DoctorCheckView(
        "state_event_commit",
        "warn",
        f"custom store state/event commit path is not verified: {detail}",
    )


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


def _resource_lock_check(
    sessions: tuple[SessionSummaryView, ...],
    metrics: RuntimeMetricsView,
) -> DoctorCheckView:
    pending_confirmations = sum(1 for session in sessions if session.pending_action)
    if metrics.active_resource_lock_count > pending_confirmations:
        return DoctorCheckView(
            "resource_locks",
            "warn",
            "active resource locks exceed pending confirmations: "
            f"locks={metrics.active_resource_lock_count} pending={pending_confirmations}",
        )
    if metrics.resource_conflict_count:
        return DoctorCheckView(
            "resource_locks",
            "warn",
            f"resource conflicts detected: {metrics.resource_conflict_count}",
        )
    return DoctorCheckView(
        "resource_locks",
        "ok",
        "resource locks balanced: "
        f"active={metrics.active_resource_lock_count} conflicts={metrics.resource_conflict_count}",
    )


def _distributed_runtime_check(
    *,
    status: str | None,
    check_count: int | None,
    capacity_gap_count: int | None,
    expiring_lease_count: int | None,
    recommendation_count: int | None,
) -> DoctorCheckView:
    if status is None:
        return DoctorCheckView(
            "distributed_runtime",
            "ok",
            "distributed runtime coordinator not configured",
        )
    if status not in {"ok", "warn", "error"}:
        return DoctorCheckView(
            "distributed_runtime",
            "error",
            f"unknown distributed health status: {status}",
        )
    return DoctorCheckView(
        "distributed_runtime",
        status,
        "status="
        f"{status} checks={check_count or 0} "
        f"capacity_gaps={capacity_gap_count or 0} "
        f"expiring_leases={expiring_lease_count or 0} "
        f"recommendations={recommendation_count or 0}",
    )


def _distributed_work_queue_check(
    *,
    invalid_session_work_item_count: int | None,
    terminal_work_item_count: int | None,
) -> DoctorCheckView:
    if invalid_session_work_item_count is None:
        return DoctorCheckView(
            "distributed_work_queue",
            "ok",
            "distributed work queue not inspected",
        )
    if invalid_session_work_item_count:
        return DoctorCheckView(
            "distributed_work_queue",
            "error",
            f"invalid_session_work_items={invalid_session_work_item_count}",
        )
    if terminal_work_item_count:
        return DoctorCheckView(
            "distributed_work_queue",
            "warn",
            f"terminal_work_items={terminal_work_item_count} prune recommended",
        )
    return DoctorCheckView(
        "distributed_work_queue",
        "ok",
        "session work items reference known sessions; terminal_work_items=0",
    )


def _runtime_config_check(
    *,
    domain_count: int,
    configured_domain_count: int | None,
    store_backend: str | None,
    max_iterations: int | None,
    max_recovery_steps: int | None,
) -> DoctorCheckView:
    if (
        configured_domain_count is None
        and store_backend is None
        and max_iterations is None
        and max_recovery_steps is None
    ):
        return DoctorCheckView("runtime_config", "ok", "runtime configuration not provided")

    errors: list[str] = []
    if configured_domain_count is not None and configured_domain_count != domain_count:
        errors.append(f"configured_domains={configured_domain_count} active_domains={domain_count}")
    if store_backend is not None and store_backend not in {"memory", "file", "sqlite"}:
        errors.append(f"unsupported store backend: {store_backend}")
    if max_iterations is not None and max_iterations < 1:
        errors.append(f"max_iterations={max_iterations}")
    if max_recovery_steps is not None and max_recovery_steps < 1:
        errors.append(f"max_recovery_steps={max_recovery_steps}")
    if errors:
        return DoctorCheckView("runtime_config", "error", "; ".join(errors))

    return DoctorCheckView(
        "runtime_config",
        "ok",
        "store="
        f"{store_backend or 'unknown'} domains={configured_domain_count or domain_count} "
        f"max_iterations={max_iterations or 'unknown'} "
        f"max_recovery_steps={max_recovery_steps or 'unknown'}",
    )


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


def _active_resource_locks(
    events: tuple[RuntimeEventView, ...],
) -> dict[tuple[ActionId, str], RuntimeEventView]:
    active: dict[tuple[ActionId, str], RuntimeEventView] = {}
    for event in sorted(events, key=lambda item: item.occurred_at):
        key = _resource_lock_key(event)
        if key is None:
            continue
        if event.type == "ResourceLockAcquired":
            active[key] = event
        elif event.type == "ResourceLockReleased":
            active.pop(key, None)
    return active


def _resource_lock_key(event: RuntimeEventView) -> tuple[ActionId, str] | None:
    if event.action_id is None:
        return None
    resource_key = _string(event.data.get("resource_key"))
    if not resource_key:
        return None
    return event.action_id, resource_key
