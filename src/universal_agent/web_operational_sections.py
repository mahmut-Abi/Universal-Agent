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
from universal_agent.web_ui import _html, _section, _status_class, _table


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
    return _section(
        "Runtime Configuration",
        '<dl class="details">'
        + "".join(f"<dt>{_html(label)}</dt><dd>{_html(value)}</dd>" for label, value in items)
        + "</dl>",
    )

def _doctor_checks(doctor: DoctorReportView) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(check.name)}</td>",
                (
                    '<td><span class="severity '
                    f'{_status_class(check.status)}">{_html(check.status)}</span></td>'
                ),
                f"<td>{_html(check.message)}</td>",
                "</tr>",
            )
        )
        for check in doctor.checks
    ]
    if not rows:
        rows.append('<tr><td colspan="3">No doctor checks</td></tr>')
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
        '<p class="empty">Distributed runtime coordinator is not configured</p>',
    )

def _distributed_health_checks(health: DistributedHealthReport | None) -> str:
    rows = []
    if health is not None:
        rows = [
            "\n".join(
                (
                    "<tr>",
                    f"<td>{_html(check.name)}</td>",
                    (
                        '<td><span class="severity '
                        f'{_status_class(check.status.value)}">'
                        f"{_html(check.status.value)}</span></td>"
                    ),
                    f"<td>{_html(check.message)}</td>",
                    "</tr>",
                )
            )
            for check in health.checks
        ]
    if not rows:
        rows.append('<tr><td colspan="3">No distributed health checks</td></tr>')
    return _section(
        "Distributed Health Checks",
        _table(("Check", "Status", "Message"), tuple(rows)),
    )

def _distributed_recommendations(health: DistributedHealthReport | None) -> str:
    rows = []
    if health is not None:
        rows = [
            "\n".join(
                (
                    "<tr>",
                    f"<td>{_html(recommendation.code)}</td>",
                    (
                        '<td><span class="severity '
                        f'{_status_class(recommendation.severity.value)}">'
                        f"{_html(recommendation.severity.value)}</span></td>"
                    ),
                    f"<td>{_html(recommendation.target or 'runtime')}</td>",
                    f"<td>{_html(recommendation.message)}</td>",
                    "</tr>",
                )
            )
            for recommendation in health.recommendations
        ]
    if not rows:
        rows.append('<tr><td colspan="4">No distributed recommendations</td></tr>')
    return _section(
        "Distributed Recommendations",
        _table(("Code", "Severity", "Target", "Message"), tuple(rows)),
    )

def _distributed_work_queue(distributed: DistributedRuntimeSnapshot | None) -> str:
    rows = []
    if distributed is not None:
        rows = [
            "\n".join(
                (
                    "<tr>",
                    f"<td>{_html(item.work_item_id)}</td>",
                    f"<td>{_html(item.kind)}</td>",
                    f"<td>{_html(item.status.value)}</td>",
                    f"<td>{_html(item.session_id or '-')}</td>",
                    f"<td>{_html(item.task_id or '-')}</td>",
                    f"<td>{_html(item.action_id or '-')}</td>",
                    f"<td>{item.priority}</td>",
                    f"<td>{item.attempts}/{item.max_attempts}</td>",
                    f"<td>{_html(item.worker_id or '-')}</td>",
                    f"<td>{_html(item.last_error or '-')}</td>",
                    "</tr>",
                )
            )
            for item in distributed.work_queue.items
        ]
    if not rows:
        rows.append('<tr><td colspan="10">No distributed work items</td></tr>')
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
            "\n".join(
                (
                    "<tr>",
                    f"<td>{_html(worker.worker_id)}</td>",
                    f"<td>{_html(worker.status.value)}</td>",
                    f"<td>{_html(', '.join(worker.capabilities) or 'none')}</td>",
                    f"<td>{_html(worker.heartbeat_at.isoformat())}</td>",
                    f"<td>{_html(worker.lease_expires_at.isoformat())}</td>",
                    f"<td>{_html(worker.last_error or '-')}</td>",
                    "</tr>",
                )
            )
            for worker in distributed.workers.workers
        ]
    if not rows:
        rows.append('<tr><td colspan="6">No distributed workers</td></tr>')
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
            "\n".join(
                (
                    "<tr>",
                    f"<td>{_html(lock.lock_key)}</td>",
                    f"<td>{_html(lock.owner_id)}</td>",
                    f"<td>{_html(lock.lease_id)}</td>",
                    f"<td>{_html(lock.heartbeat_at.isoformat())}</td>",
                    f"<td>{_html(lock.lease_expires_at.isoformat())}</td>",
                    f"<td>{_html(_value_text(lock.metadata))}</td>",
                    "</tr>",
                )
            )
            for lock in distributed.locks
        ]
    if not rows:
        rows.append('<tr><td colspan="6">No distributed locks</td></tr>')
    return _section(
        "Distributed Locks",
        _table(("Lock", "Owner", "Lease", "Heartbeat", "Lease Expires", "Metadata"), tuple(rows)),
    )

def _multi_agent(multi_agent: MultiAgentView) -> str:
    if not multi_agent.enabled:
        return _section(
            "Multi-Agent",
            '<p class="empty">Multi-Agent registry is not configured</p>',
        )
    profile_rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(profile.name)}@{_html(profile.version)}</td>",
                f"<td>{_html(_identity_tuple_text(profile.domains))}</td>",
                f"<td>{_html(_string_tuple_text(profile.permissions))}</td>",
                f"<td>{_html(_string_tuple_text(profile.capabilities))}</td>",
                f"<td>{_html(profile.description)}</td>",
                "</tr>",
            )
        )
        for profile in multi_agent.profiles
    ]
    instance_rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(instance.agent_id)}</td>",
                f"<td>{_html(instance.profile_name)}@{_html(instance.profile_version)}</td>",
                f"<td>{_html(instance.status.value)}</td>",
                f"<td>{_html(instance.session_id or 'none')}</td>",
                f"<td>{_html(instance.endpoint or 'none')}</td>",
                "</tr>",
            )
        )
        for instance in multi_agent.instances
    ]
    task_rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(task.task_id)}</td>",
                f"<td>{task.child_count}</td>",
                f"<td>{_html(_delegation_depth_text(task.delegation_depth))}</td>",
                "</tr>",
            )
        )
        for task in multi_agent.delegation_tasks
    ]
    if not profile_rows:
        profile_rows.append('<tr><td colspan="5">No agent profiles</td></tr>')
    if not instance_rows:
        instance_rows.append('<tr><td colspan="5">No agent instances</td></tr>')
    if not task_rows:
        task_rows.append('<tr><td colspan="3">No delegation tasks</td></tr>')
    return _section(
        "Multi-Agent",
        _table(
            ("Profile", "Domains", "Permissions", "Capabilities", "Description"),
            tuple(profile_rows),
        )
        + _table(("Agent", "Profile", "Status", "Session", "Endpoint"), tuple(instance_rows))
        + _table(("Task", "Children", "Depth"), tuple(task_rows)),
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
    return "\n".join(
        (
            "<tr>",
            f'<td><span class="severity {severity}">{_html(severity)}</span></td>',
            f"<td>{_html(signal)}</td>",
            f"<td>{_html(value)}</td>",
            f"<td>{_html(reason)}</td>",
            "</tr>",
        )
    )

