from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from universal_agent.console import RuntimeConsoleSnapshot, build_runtime_console_snapshot
from universal_agent.core import SessionId
from universal_agent.operations import AuditRecordView
from universal_agent.runtime import RuntimeEventView, SessionSummaryView, SessionView
from universal_agent.service import DomainView, ReadyView, RuntimeService

TuiSnapshot = RuntimeConsoleSnapshot


async def build_tui_snapshot(
    service: RuntimeService,
    *,
    session_id: SessionId | None = None,
    session_limit: int = 5,
    event_limit: int = 12,
) -> TuiSnapshot:
    """Build a read-only TUI snapshot from RuntimeService-facing projections."""

    return await build_runtime_console_snapshot(
        service,
        session_id=session_id,
        session_limit=session_limit,
        event_limit=event_limit,
    )


def render_tui_snapshot(snapshot: TuiSnapshot) -> str:
    lines = [
        "Universal Agent Runtime TUI",
        _rule(),
        f"Health: {snapshot.health.status} | Ready: {_ready_text(snapshot.ready)}",
        (
            f"Runtime: store={snapshot.config.store_backend}"
            f" max_iterations={snapshot.config.max_iterations}"
            f" max_recovery_steps={snapshot.config.max_recovery_steps}"
        ),
        (
            f"Metrics: sessions={snapshot.metrics.session_count}"
            f" active={snapshot.metrics.active_session_count}"
            f" waiting={snapshot.metrics.waiting_session_count}"
            f" events={snapshot.metrics.event_count}"
            " actions="
            f"{snapshot.metrics.action_started_count}/"
            f"{snapshot.metrics.action_completed_count}"
            f" policy_denials={snapshot.metrics.policy_denial_count}"
            f" recoveries={snapshot.metrics.recovery_planned_count}"
        ),
        (
            f"Cost: calls={snapshot.cost.model_call_count}"
            f" tokens={snapshot.cost.total_tokens}"
            f" estimated_cost_micros={snapshot.cost.estimated_cost_micros}"
            f" currency={snapshot.cost.currency}"
        ),
        "",
        "Active Domains",
        _rule(),
    ]
    lines.extend(_domain_lines(snapshot.domains))
    lines.extend(("", "Sessions", _rule()))
    lines.extend(_session_lines(snapshot.sessions))
    lines.extend(("", "Selected Session", _rule()))
    lines.extend(_selected_session_lines(snapshot.selected_session))
    lines.extend(("", "Recent Events", _rule()))
    lines.extend(_event_lines(snapshot.events))
    lines.extend(("", "Audit", _rule()))
    lines.extend(_audit_lines(snapshot.audit_records))
    return "\n".join(lines) + "\n"


def _domain_lines(domains: tuple[DomainView, ...]) -> list[str]:
    if not domains:
        return ["- none"]
    return [
        (
            f"- {'*' if domain.primary else ' '} {domain.name}@{domain.version}"
            f" capabilities={len(domain.capability_names)}"
            f" evaluators={len(domain.evaluator_names)}"
        )
        for domain in domains
    ]


def _session_lines(sessions: tuple[SessionSummaryView, ...]) -> list[str]:
    if not sessions:
        return ["- none"]
    return [
        (
            f"- {session.session_id} goal={session.goal_status.value}"
            f" task={session.current_task_status.value}"
            f" iter={session.iteration}"
            f" pending_action={session.pending_action}"
            f" domain={session.domain_name}@{session.domain_version}"
            f" :: {session.goal_description}"
        )
        for session in sessions
    ]


def _selected_session_lines(session: SessionView | None) -> list[str]:
    if session is None:
        return ["- none"]
    lines = [
        f"Session: {session.session_id}",
        f"Goal: {session.goal_status.value} :: {session.goal_description}",
        f"Current Task: {session.current_task_status.value} :: {session.current_task_description}",
        f"Iteration: {session.iteration} | Tasks: {len(session.tasks)}",
        f"Domain: {session.domain_name}@{session.domain_version}",
        "Satisfied Criteria: " + _mapping_text(session.satisfied_criteria),
        "Pending Action: " + _pending_action_text(session),
        "Latest Evaluation: " + _evaluation_text(session),
    ]
    if session.termination_reason is not None:
        lines.append(f"Termination: {session.termination_reason}")
    if session.error_code is not None:
        lines.append(f"Error: {session.error_code.value}")
    return lines


def _event_lines(events: tuple[RuntimeEventView, ...]) -> list[str]:
    if not events:
        return ["- none"]
    return [
        (
            f"- {event.occurred_at.isoformat()} {event.type}"
            f" task={event.task_id}"
            f" action={event.action_id or '-'}"
            f" {_event_detail(event.data)}"
        ).rstrip()
        for event in events
    ]


def _audit_lines(records: tuple[AuditRecordView, ...]) -> list[str]:
    if not records:
        return ["- none"]
    return [
        (
            f"- {record.occurred_at.isoformat()} capability={record.capability}"
            f" tool={record.tool_name}"
            f" policy={record.policy_effect}:{record.policy_name}"
            f" status={record.status}"
        )
        for record in records
    ]


def _pending_action_text(session: SessionView) -> str:
    action = session.pending_action
    if action is None:
        return "none"
    return (
        f"{action.action_id} capability={action.capability}"
        f" tool={action.tool_name}"
        f" resource={action.resource_key}"
        f" attempt={action.attempt}"
    )


def _evaluation_text(session: SessionView) -> str:
    evaluation = session.latest_evaluation
    if evaluation is None:
        return "none"
    return (
        f"{evaluation.status.value}"
        f" task_completed={evaluation.task_completed}"
        f" goal_completed={evaluation.goal_completed}"
        f" evaluator={evaluation.evaluator_name}"
        f" reason={evaluation.reason}"
    )


def _event_detail(data: Mapping[str, Any]) -> str:
    keys = (
        "decision_type",
        "capability",
        "tool_name",
        "effect",
        "status",
        "error_code",
        "observation_id",
        "evidence_id",
        "claim",
        "reason",
    )
    parts = [f"{key}={data[key]}" for key in keys if key in data]
    return " ".join(parts)


def _mapping_text(values: Mapping[str, Any]) -> str:
    if not values:
        return "none"
    return ", ".join(f"{key}={values[key]}" for key in sorted(values))


def _ready_text(ready: ReadyView) -> str:
    return "yes" if ready.ready else f"no ({ready.reason})"


def _rule() -> str:
    return "-" * 32


__all__ = ["TuiSnapshot", "build_tui_snapshot", "render_tui_snapshot"]
