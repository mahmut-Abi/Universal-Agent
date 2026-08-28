from __future__ import annotations

from universal_agent.distributed import DistributedHealthReport, DistributedRuntimeSnapshot
from universal_agent.operations import DoctorReportView
from universal_agent.service import MultiAgentView
from universal_agent.web_helpers import (
    _identity_tuple_text,
    _ready_text,
    _retention_text,
    _string_tuple_text,
    _value_text,
)
from universal_agent.web_types import WebConsoleSnapshot
from universal_agent.web_ui import (
    _detail_list,
    _empty_paragraph,
    _empty_table_row,
    _raw_table_cell,
    _section,
    _section_blocks,
    _span,
    _status_class,
    _table,
    _table_row,
)


def _runtime_settings(snapshot: WebConsoleSnapshot) -> str:
    items = (
        ("Store Backend", snapshot.config.store_backend),
        ("Store Path", snapshot.config.store_path or "memory"),
        ("State/Event Commit", _state_event_commit_text(snapshot)),
        ("Model Provider", snapshot.config.model.provider),
        ("Model Name", snapshot.config.model.name),
        ("Model Endpoint", snapshot.config.model.endpoint or "not configured"),
        ("Model API Key Secret", snapshot.config.model.api_key_secret or "not configured"),
        ("Model Timeout Seconds", f"{snapshot.config.model.timeout_seconds:g}"),
        ("Distributed Queue Backend", snapshot.config.distributed_queue_backend),
        ("Distributed Queue Path", snapshot.config.distributed_queue_path or "memory"),
        ("Distributed Locks Backend", snapshot.config.distributed_locks_backend),
        ("Distributed Locks Path", snapshot.config.distributed_locks_path or "memory"),
        ("Distributed Workers Backend", snapshot.config.distributed_workers_backend),
        ("Distributed Workers Path", snapshot.config.distributed_workers_path or "memory"),
        (
            "Distributed Terminal Retention",
            _retention_text(snapshot.config.distributed_terminal_retention_seconds),
        ),
        ("Max Iterations", str(snapshot.config.max_iterations)),
        ("Max Recovery Steps", str(snapshot.config.max_recovery_steps)),
        ("Health", snapshot.health.status),
        ("Ready", _ready_text(snapshot)),
    )
    return _section("Runtime Configuration", _detail_list(items))


def _doctor_checks(doctor: DoctorReportView) -> str:
    rows = [
        _table_row(
            (
                check.name,
                _raw_table_cell(
                    _span(check.status, class_name=f"severity {_status_class(check.status)}")
                ),
                check.message,
            )
        )
        for check in doctor.checks
    ]
    if not rows:
        rows.append(_empty_table_row("No doctor checks", colspan=3))
    return _section(
        "Doctor Checks",
        _table(("Check", "Status", "Message"), tuple(rows)),
    )


def _state_event_commit_text(snapshot: WebConsoleSnapshot) -> str:
    supported = snapshot.config.state_event_commit_supported
    strategy = snapshot.config.state_event_commit_strategy or "unknown"
    shared_store = snapshot.config.state_event_commit_shared_store
    if supported is None:
        return "unknown"
    status = "enabled" if supported and shared_store else "split"
    return f"{status} ({strategy})"


def _distributed_not_configured(distributed: DistributedRuntimeSnapshot | None) -> str:
    if distributed is not None:
        return ""
    return _section(
        "Distributed Runtime",
        _empty_paragraph("Distributed runtime coordinator is not configured"),
    )


def _distributed_health_checks(health: DistributedHealthReport | None) -> str:
    rows = []
    if health is not None:
        rows = [
            _table_row(
                (
                    check.name,
                    _raw_table_cell(
                        _span(
                            check.status.value,
                            class_name=f"severity {_status_class(check.status.value)}",
                        )
                    ),
                    check.message,
                )
            )
            for check in health.checks
        ]
    if not rows:
        rows.append(_empty_table_row("No distributed health checks", colspan=3))
    return _section(
        "Distributed Health Checks",
        _table(("Check", "Status", "Message"), tuple(rows)),
    )


def _distributed_recommendations(health: DistributedHealthReport | None) -> str:
    rows = []
    if health is not None:
        rows = [
            _table_row(
                (
                    recommendation.code,
                    _raw_table_cell(
                        _span(
                            recommendation.severity.value,
                            class_name=f"severity {_status_class(recommendation.severity.value)}",
                        )
                    ),
                    recommendation.target or "runtime",
                    recommendation.message,
                )
            )
            for recommendation in health.recommendations
        ]
    if not rows:
        rows.append(_empty_table_row("No distributed recommendations", colspan=4))
    return _section(
        "Distributed Recommendations",
        _table(("Code", "Severity", "Target", "Message"), tuple(rows)),
    )


def _distributed_work_queue(distributed: DistributedRuntimeSnapshot | None) -> str:
    rows = []
    if distributed is not None:
        rows = [
            _table_row(
                (
                    item.work_item_id,
                    item.kind,
                    item.status.value,
                    item.session_id or "-",
                    item.task_id or "-",
                    item.action_id or "-",
                    item.priority,
                    f"{item.attempts}/{item.max_attempts}",
                    item.worker_id or "-",
                    item.last_error or "-",
                )
            )
            for item in distributed.work_queue.items
        ]
    if not rows:
        rows.append(_empty_table_row("No distributed work items", colspan=10))
    return _section(
        "Distributed Work Queue",
        _table(
            (
                "Work Item",
                "Kind",
                "Status",
                "Session",
                "Task",
                "Action",
                "Priority",
                "Attempts",
                "Worker",
                "Last Error",
            ),
            tuple(rows),
        ),
    )


def _distributed_workers(distributed: DistributedRuntimeSnapshot | None) -> str:
    rows = []
    if distributed is not None:
        rows = [
            _table_row(
                (
                    worker.worker_id,
                    worker.status.value,
                    ", ".join(worker.capabilities) or "none",
                    worker.heartbeat_at.isoformat(),
                    worker.lease_expires_at.isoformat(),
                    worker.last_error or "-",
                )
            )
            for worker in distributed.workers.workers
        ]
    if not rows:
        rows.append(_empty_table_row("No distributed workers", colspan=6))
    return _section(
        "Distributed Workers",
        _table(
            ("Worker", "Status", "Capabilities", "Heartbeat", "Lease Expires", "Last Error"),
            tuple(rows),
        ),
    )


def _distributed_locks(distributed: DistributedRuntimeSnapshot | None) -> str:
    rows = []
    if distributed is not None:
        rows = [
            _table_row(
                (
                    lock.lock_key,
                    lock.owner_id,
                    lock.lease_id,
                    lock.heartbeat_at.isoformat(),
                    lock.lease_expires_at.isoformat(),
                    _value_text(lock.metadata),
                )
            )
            for lock in distributed.locks
        ]
    if not rows:
        rows.append(_empty_table_row("No distributed locks", colspan=6))
    return _section(
        "Distributed Locks",
        _table(("Lock", "Owner", "Lease", "Heartbeat", "Lease Expires", "Metadata"), tuple(rows)),
    )


def _multi_agent(multi_agent: MultiAgentView) -> str:
    if not multi_agent.enabled:
        return _section(
            "Multi-Agent",
            _empty_paragraph("Multi-Agent registry is not configured"),
        )
    profile_rows = [
        _table_row(
            (
                f"{profile.name}@{profile.version}",
                _identity_tuple_text(profile.domains),
                _string_tuple_text(profile.permissions),
                _string_tuple_text(profile.capabilities),
                profile.description,
            )
        )
        for profile in multi_agent.profiles
    ]
    instance_rows = [
        _table_row(
            (
                instance.agent_id,
                f"{instance.profile_name}@{instance.profile_version}",
                instance.status.value,
                instance.session_id or "none",
                instance.endpoint or "none",
            )
        )
        for instance in multi_agent.instances
    ]
    task_rows = [
        _table_row(
            (
                task.task_id,
                task.child_count,
                _delegation_depth_text(task.delegation_depth),
            )
        )
        for task in multi_agent.delegation_tasks
    ]
    if not profile_rows:
        profile_rows.append(_empty_table_row("No agent profiles", colspan=5))
    if not instance_rows:
        instance_rows.append(_empty_table_row("No agent instances", colspan=5))
    if not task_rows:
        task_rows.append(_empty_table_row("No delegation tasks", colspan=3))
    return _section_blocks(
        "Multi-Agent",
        (
            _table(
                ("Profile", "Domains", "Permissions", "Capabilities", "Description"),
                tuple(profile_rows),
            ),
            _table(("Agent", "Profile", "Status", "Session", "Endpoint"), tuple(instance_rows)),
            _table(("Task", "Children", "Depth"), tuple(task_rows)),
        ),
    )


def _delegation_depth_text(delegation_depth: int | None) -> str:
    if delegation_depth is None:
        return "unknown"
    return str(delegation_depth)


def _operational_diagnostics(snapshot: WebConsoleSnapshot) -> str:
    rows = tuple(_operational_diagnostic_rows(snapshot))
    if not rows:
        rows = (
            _diagnostic_row(
                "ok",
                "runtime",
                "operational",
                "No active operational issues",
            ),
        )
    return _section(
        "Operational Diagnostics",
        _table(("Severity", "Signal", "Value", "Reason"), rows),
    )


def _operational_diagnostic_rows(snapshot: WebConsoleSnapshot) -> list[str]:
    metrics = snapshot.metrics
    rows: list[str] = []
    if not snapshot.ready.ready:
        rows.append(_diagnostic_row("error", "ready", "no", snapshot.ready.reason))
    if metrics.failed_goal_count:
        rows.append(_diagnostic_row("error", "failed_goals", metrics.failed_goal_count))
    if metrics.tool_failure_count:
        rows.append(_diagnostic_row("error", "tool_failures", metrics.tool_failure_count))
    if metrics.decision_rejected_count:
        rows.append(_diagnostic_row("error", "decisions_rejected", metrics.decision_rejected_count))
    if metrics.recovery_exhausted_count:
        rows.append(
            _diagnostic_row("error", "recovery_exhausted", metrics.recovery_exhausted_count)
        )
    if metrics.policy_denial_count:
        rows.append(_diagnostic_row("warn", "policy_denials", metrics.policy_denial_count))
    if metrics.confirmation_required_count:
        rows.append(
            _diagnostic_row("warn", "confirmations_required", metrics.confirmation_required_count)
        )
    if metrics.human_intervention_count:
        rows.append(
            _diagnostic_row("warn", "human_interventions", metrics.human_intervention_count)
        )
    if metrics.resource_conflict_count:
        rows.append(_diagnostic_row("warn", "resource_conflicts", metrics.resource_conflict_count))
    if metrics.active_resource_lock_count:
        rows.append(
            _diagnostic_row("warn", "active_resource_locks", metrics.active_resource_lock_count)
        )
    if metrics.waiting_session_count:
        rows.append(_diagnostic_row("info", "waiting_sessions", metrics.waiting_session_count))
    if metrics.recovery_planned_count:
        rows.append(_diagnostic_row("info", "recoveries_planned", metrics.recovery_planned_count))
    return rows


def _diagnostic_row(
    severity: str,
    signal: str,
    value: object,
    reason: str = "",
) -> str:
    return _table_row(
        (
            _raw_table_cell(_span(severity, class_name=f"severity {severity}")),
            signal,
            value,
            reason,
        )
    )
