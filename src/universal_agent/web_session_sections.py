from __future__ import annotations

from universal_agent.operations import AuditRecordView
from universal_agent.runtime import RuntimeEventView, SessionSummaryView, SessionView
from universal_agent.web_helpers import _event_detail, _mapping_text, _string_tuple_text
from universal_agent.web_ui import _attr, _html, _section, _table


def _sessions(sessions: tuple[SessionSummaryView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                (
                    '<td><a href="/console/sessions/'
                    f'{_attr(session.session_id)}">{_html(session.session_id)}</a></td>'
                ),
                f"<td>{_html(session.goal_status.value)}</td>",
                f"<td>{_html(session.current_task_status.value)}</td>",
                f"<td>{session.iteration}</td>",
                f"<td>{_html(session.domain_name)}@{_html(session.domain_version)}</td>",
                f"<td>{_html(session.goal_description)}</td>",
                "</tr>",
            )
        )
        for session in sessions
    ]
    if not rows:
        rows.append('<tr><td colspan="6">No sessions</td></tr>')
    return _section(
        "Sessions",
        _table(
            ("Session", "Goal", "Task", "Iter", "Domain", "Description"),
            tuple(rows),
        ),
    )


def _selected_session(session: SessionView | None) -> str:
    if session is None:
        return _section("Selected Session", '<p class="empty">No selected session</p>')
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
    return _section(
        "Selected Session",
        '<dl class="details">'
        + "".join(f"<dt>{_html(label)}</dt><dd>{_html(value)}</dd>" for label, value in items)
        + "</dl>",
    )


def _task_timeline(session: SessionView | None) -> str:
    rows = []
    if session is not None:
        rows = [
            "\n".join(
                (
                    "<tr>",
                    f"<td>{_html(task.task_id)}</td>",
                    f"<td>{_html(task.status.value)}</td>",
                    f"<td>{_html(task.description)}</td>",
                    f"<td>{_html(_string_tuple_text(task.required_criteria))}</td>",
                    f"<td>{_html(_string_tuple_text(task.depends_on))}</td>",
                    "</tr>",
                )
            )
            for task in session.tasks
        ]
    if not rows:
        rows.append('<tr><td colspan="5">No tasks</td></tr>')
    return _section(
        "Task Timeline",
        _table(("Task", "Status", "Description", "Required Criteria", "Depends On"), tuple(rows)),
    )


def _events(events: tuple[RuntimeEventView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(event.occurred_at.isoformat())}</td>",
                f"<td>{_html(event.type)}</td>",
                f"<td>{_html(event.task_id)}</td>",
                f"<td>{_html(event.action_id or '-')}</td>",
                f"<td>{_html(_event_detail(event.data))}</td>",
                "</tr>",
            )
        )
        for event in events
    ]
    if not rows:
        rows.append('<tr><td colspan="5">No events</td></tr>')
    return _section(
        "Recent Events",
        _table(("Time", "Type", "Task", "Action", "Detail"), tuple(rows)),
    )


def _audit(records: tuple[AuditRecordView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(record.occurred_at.isoformat())}</td>",
                f"<td>{_html(record.capability)}</td>",
                f"<td>{_html(record.tool_name)}</td>",
                f"<td>{_html(record.policy_effect)}:{_html(record.policy_name)}</td>",
                f"<td>{_html(record.status)}</td>",
                "</tr>",
            )
        )
        for record in records
    ]
    if not rows:
        rows.append('<tr><td colspan="5">No audit records</td></tr>')
    return _section(
        "Audit",
        _table(("Time", "Capability", "Tool", "Policy", "Status"), tuple(rows)),
    )
