from __future__ import annotations

from html import escape as escape_html

from universal_agent.core import GoalStatus
from universal_agent.operations import AuditRecordView
from universal_agent.runtime import RuntimeEventView, SessionSummaryView, SessionView
from universal_agent.web.helpers import _event_detail, _mapping_text, _string_tuple_text
from universal_agent.web.ui import (
    _detail_list,
    _empty_paragraph,
    _link,
    _raw_table_cell,
    _section,
    _table_section,
)


def render_session_operator_actions(session: SessionView | None) -> str:
    """Render controlled operator action forms for the selected session.

    Forms POST to console action routes that dispatch through the same
    RuntimeService methods the CLI and agentd use, so policy checks and the
    pending-action confirmation boundary stay identical across surfaces:

    - WAITING with a pending action: explicit confirm-and-resume and reject
      forms (the CLI ``resume --confirmed`` boundary).
    - WAITING without a pending action: plain resume.
    - RUNNING: pause and cancel.
    """
    if session is None:
        return ""
    status = session.goal_status
    if status in (
        GoalStatus.PENDING,
        GoalStatus.COMPLETED,
        GoalStatus.FAILED,
        GoalStatus.CANCELLED,
    ):
        return ""
    session_path = f"/console/sessions/{escape_html(str(session.session_id), quote=True)}"
    pending = session.pending_action
    blocks: list[str] = []
    if status is GoalStatus.WAITING and pending is not None:
        target = f" on <code>{escape_html(pending.target)}</code>" if pending.target else ""
        blocks.append(
            "<p>Pending action <strong>"
            f"{escape_html(pending.capability)}</strong> via "
            f"<code>{escape_html(pending.tool_name)}</code>{target} "
            f"(attempt {pending.attempt}) requires operator confirmation.</p>"
        )
        blocks.append(
            _action_form(
                f"{session_path}/resume",
                "Confirm &amp; resume",
                confirmed="true",
            )
        )
        blocks.append(
            _action_form(
                f"{session_path}/resume",
                "Reject pending action",
                confirmed="false",
            )
        )
    elif status is GoalStatus.WAITING:
        blocks.append(_action_form(f"{session_path}/resume", "Resume"))
    if status is GoalStatus.RUNNING:
        blocks.append(
            _action_form(
                f"{session_path}/pause",
                "Pause",
                text_name="reason",
                text_label="Reason",
            )
        )
    blocks.append(
        _action_form(
            f"{session_path}/cancel",
            "Cancel",
            text_name="reason",
            text_label="Reason",
        )
    )
    body = "".join(f'<div class="operator-action">{block}</div>' for block in blocks)
    return _section("Operator actions", body)


def _action_form(
    action: str,
    label: str,
    *,
    confirmed: str | None = None,
    text_name: str | None = None,
    text_label: str | None = None,
) -> str:
    hidden = (
        f'<input type="hidden" name="confirmed" value="{escape_html(confirmed, quote=True)}" />'
        if confirmed is not None
        else ""
    )
    text = (
        f'<label for="{text_name}-input">{text_label}</label>'
        f'<input id="{text_name}-input" name="{text_name}" type="text" />'
        if text_name is not None
        else ""
    )
    return (
        f'<form method="post" action="{escape_html(action, quote=True)}">'
        f"{hidden}{text}<button type='submit'>{label}</button></form>"
    )


def _sessions(sessions: tuple[SessionSummaryView, ...]) -> str:
    return _table_section(
        "Sessions",
        ("Session", "Goal", "Task", "Iter", "Domain", "Description"),
        (
            (
                _raw_table_cell(
                    _link(session.session_id, f"/console/sessions/{session.session_id}")
                ),
                session.goal_status.value,
                session.current_task_status.value,
                session.iteration,
                f"{session.domain_name}@{session.domain_version}",
                session.goal_description,
            )
            for session in sessions
        ),
        empty_message="No sessions",
    )


def _selected_session(session: SessionView | None) -> str:
    if session is None:
        return _section("Selected Session", _empty_paragraph("No selected session"))
    pending = "none"
    if session.pending_action is not None:
        pending = (
            f"{session.pending_action.capability} "
            f"tool={session.pending_action.tool_name} "
            f"attempt={session.pending_action.attempt}"
        )
    latest = "none"
    if session.latest_evaluation is not None:
        latest = (
            f"{session.latest_evaluation.status.value} "
            f"task_completed={session.latest_evaluation.task_completed} "
            f"goal_completed={session.latest_evaluation.goal_completed} "
            f"reason={session.latest_evaluation.reason}"
        )
    items = (
        ("Session", str(session.session_id)),
        ("Goal", f"{session.goal_status.value}: {session.goal_description}"),
        (
            "Current Task",
            f"{session.current_task_status.value}: {session.current_task_description}",
        ),
        ("Iteration", str(session.iteration)),
        ("Domain", f"{session.domain_name}@{session.domain_version}"),
        ("Satisfied Criteria", _mapping_text(session.satisfied_criteria)),
        ("Pending Action", pending),
        ("Latest Evaluation", latest),
    )
    return _section("Selected Session", _detail_list(items))


def _task_timeline(session: SessionView | None) -> str:
    tasks = () if session is None else session.tasks
    return _table_section(
        "Task Timeline",
        ("Task", "Status", "Description", "Required Criteria", "Depends On"),
        (
            (
                task.task_id,
                task.status.value,
                task.description,
                _string_tuple_text(task.required_criteria),
                _string_tuple_text(task.depends_on),
            )
            for task in tasks
        ),
        empty_message="No tasks",
    )


def _events(events: tuple[RuntimeEventView, ...]) -> str:
    return _table_section(
        "Recent Events",
        ("Time", "Type", "Task", "Action", "Detail"),
        (
            (
                event.occurred_at.isoformat(),
                event.type,
                event.task_id,
                event.action_id or "-",
                _event_detail(event.data),
            )
            for event in events
        ),
        empty_message="No events",
    )


def _audit(records: tuple[AuditRecordView, ...]) -> str:
    return _table_section(
        "Audit",
        ("Time", "Capability", "Tool", "Policy", "Status"),
        (
            (
                record.occurred_at.isoformat(),
                record.capability,
                record.tool_name,
                f"{record.policy_effect}:{record.policy_name}",
                record.status,
            )
            for record in records
        ),
        empty_message="No audit records",
    )
