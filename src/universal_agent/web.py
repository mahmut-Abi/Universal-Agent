from __future__ import annotations

import json
from collections.abc import Mapping
from html import escape
from typing import Any

from universal_agent.console import RuntimeConsoleSnapshot, build_runtime_console_snapshot
from universal_agent.core import SessionId
from universal_agent.operations import AuditRecordView
from universal_agent.runtime import RuntimeEventView, SessionSummaryView, SessionView
from universal_agent.service import (
    CapabilityView,
    EvaluatorView,
    PolicyView,
    ProfileView,
    RuntimeService,
    SessionExplorerView,
    ToolView,
)

WebConsoleSnapshot = RuntimeConsoleSnapshot


async def build_web_console_snapshot(
    service: RuntimeService,
    *,
    session_id: SessionId | None = None,
    session_limit: int = 10,
    event_limit: int = 20,
) -> WebConsoleSnapshot:
    """Build a read-only Web Console snapshot from RuntimeService projections."""

    return await build_runtime_console_snapshot(
        service,
        session_id=session_id,
        session_limit=session_limit,
        event_limit=event_limit,
    )


def render_web_console(snapshot: WebConsoleSnapshot) -> str:
    title = "Universal Agent Runtime Console"
    return "\n".join(
        (
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{_html(title)}</title>",
            f"<style>{_stylesheet()}</style>",
            "</head>",
            "<body>",
            '<main class="shell">',
            _hero(snapshot),
            '<section class="grid cards" aria-label="Runtime summary">',
            _metric_card("Sessions", snapshot.metrics.session_count),
            _metric_card("Active", snapshot.metrics.active_session_count),
            _metric_card("Events", snapshot.metrics.event_count),
            _metric_card("Actions", _action_count(snapshot)),
            _metric_card("Tokens", snapshot.cost.total_tokens),
            _metric_card("Cost micros", snapshot.cost.estimated_cost_micros),
            "</section>",
            _domains(snapshot),
            _profiles(snapshot.profiles),
            _capabilities(snapshot.capabilities),
            _tools(snapshot.tools),
            _policies(snapshot.policies),
            _evaluators(snapshot.evaluators),
            _sessions(snapshot.sessions),
            _selected_session(snapshot.selected_session),
            _world_facts(snapshot.session_explorer),
            _evidence(snapshot.session_explorer),
            _events(snapshot.events),
            _audit(snapshot.audit_records),
            "</main>",
            "</body>",
            "</html>",
        )
    )


def _hero(snapshot: WebConsoleSnapshot) -> str:
    ready_class = "ok" if snapshot.ready.ready else "warn"
    return "\n".join(
        (
            '<section class="hero">',
            "<div>",
            "<p>Universal Agent Runtime</p>",
            "<h1>Runtime Console</h1>",
            (
                "<span>"
                f"store={_html(snapshot.config.store_backend)} "
                f"max_iterations={snapshot.config.max_iterations} "
                f"max_recovery_steps={snapshot.config.max_recovery_steps}"
                "</span>"
            ),
            "</div>",
            '<div class="status">',
            f'<span class="pill ok">Health: {_html(snapshot.health.status)}</span>',
            f'<span class="pill {ready_class}">Ready: {_ready_text(snapshot)}</span>',
            "</div>",
            "</section>",
        )
    )


def _domains(snapshot: WebConsoleSnapshot) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(domain.name)}@{_html(domain.version)}</td>",
                f"<td>{'yes' if domain.primary else 'no'}</td>",
                f"<td>{len(domain.capability_names)}</td>",
                f"<td>{len(domain.evaluator_names)}</td>",
                "</tr>",
            )
        )
        for domain in snapshot.domains
    ]
    if not rows:
        rows.append('<tr><td colspan="4">No active domains</td></tr>')
    return _section(
        "Active Domains",
        _table(
            ("Domain", "Primary", "Capabilities", "Evaluators"),
            tuple(rows),
        ),
    )


def _profiles(profiles: tuple[ProfileView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(profile.name)}</td>",
                f"<td>{_html(profile.version)}</td>",
                f"<td>{_html(profile.domain_name)}@{_html(profile.domain_version)}</td>",
                f"<td>{_html(_profile_domain_text(profile))}</td>",
                f"<td>{_html(profile.description)}</td>",
                "</tr>",
            )
        )
        for profile in profiles
    ]
    if not rows:
        rows.append('<tr><td colspan="5">No profiles</td></tr>')
    return _section(
        "Profile Catalog",
        _table(("Profile", "Version", "Primary Domain", "Domains", "Description"), tuple(rows)),
    )


def _capabilities(capabilities: tuple[CapabilityView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(capability.name)}</td>",
                f"<td>{_html(capability.category.value)}</td>",
                f"<td>{_html(capability.risk.value)}</td>",
                (
                    f"<td>{_html(capability.domain_name)}@"
                    f"{_html(capability.domain_version)}</td>"
                ),
                f"<td>{_html(', '.join(capability.tool_names))}</td>",
                f"<td>{_html(capability.description)}</td>",
                "</tr>",
            )
        )
        for capability in capabilities
    ]
    if not rows:
        rows.append('<tr><td colspan="6">No capabilities</td></tr>')
    return _section(
        "Capability Catalog",
        _table(("Capability", "Category", "Risk", "Domain", "Tools", "Description"), tuple(rows)),
    )


def _tools(tools: tuple[ToolView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(tool.name)}</td>",
                f"<td>{_html(tool.side_effect.value)}</td>",
                f"<td>{_html(tool.risk.value)}</td>",
                f"<td>{_html(', '.join(tool.capabilities))}</td>",
                f"<td>{_html(', '.join(tool.required_arguments))}</td>",
                f"<td>{tool.timeout_seconds:g}s</td>",
                f"<td>{_html(tool.domain_name)}@{_html(tool.domain_version)}</td>",
                "</tr>",
            )
        )
        for tool in tools
    ]
    if not rows:
        rows.append('<tr><td colspan="7">No tools</td></tr>')
    return _section(
        "Tool Catalog",
        _table(
            ("Tool", "Side Effect", "Risk", "Capabilities", "Required Args", "Timeout", "Domain"),
            tuple(rows),
        ),
    )


def _policies(policies: tuple[PolicyView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(policy.name)}</td>",
                f"<td>{_html(policy.policy_type)}</td>",
                f"<td>{_html('n/a' if policy.effect is None else policy.effect.value)}</td>",
                f"<td>{_html(_enum_tuple_text(policy.categories))}</td>",
                f"<td>{_html(_enum_tuple_text(policy.risks))}</td>",
                f"<td>{_html(', '.join(policy.capability_names))}</td>",
                f"<td>{_html(policy.domain_name)}@{_html(policy.domain_version)}</td>",
                f"<td>{_html(policy.description)}</td>",
                "</tr>",
            )
        )
        for policy in policies
    ]
    if not rows:
        rows.append('<tr><td colspan="8">No policies</td></tr>')
    return _section(
        "Policy Catalog",
        _table(
            ("Policy", "Type", "Effect", "Categories", "Risks", "Capabilities", "Domain", "Reason"),
            tuple(rows),
        ),
    )


def _evaluators(evaluators: tuple[EvaluatorView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(evaluator.name)}</td>",
                f"<td>{_html(evaluator.evaluator_type)}</td>",
                f"<td>{_html(evaluator.domain_name)}@{_html(evaluator.domain_version)}</td>",
                "</tr>",
            )
        )
        for evaluator in evaluators
    ]
    if not rows:
        rows.append('<tr><td colspan="3">No evaluators</td></tr>')
    return _section(
        "Evaluator Catalog",
        _table(("Evaluator", "Type", "Domain"), tuple(rows)),
    )


def _sessions(sessions: tuple[SessionSummaryView, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                (
                    '<td><a href="/console?session_id='
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


def _world_facts(explorer: SessionExplorerView | None) -> str:
    rows = []
    if explorer is not None:
        rows = [
            "\n".join(
                (
                    "<tr>",
                    f"<td>{_html(fact.subject)}</td>",
                    f"<td>{_html(fact.claim)}</td>",
                    f"<td>{_html(_value_text(fact.value))}</td>",
                    f"<td>{fact.confidence:.2f}</td>",
                    f"<td>{_html(', '.join(fact.evidence_ids))}</td>",
                    "</tr>",
                )
            )
            for fact in explorer.world_facts
        ]
    if not rows:
        rows.append('<tr><td colspan="5">No world facts</td></tr>')
    return _section(
        "World Facts",
        _table(("Subject", "Claim", "Value", "Confidence", "Evidence"), tuple(rows)),
    )


def _evidence(explorer: SessionExplorerView | None) -> str:
    rows = []
    if explorer is not None:
        rows = [
            "\n".join(
                (
                    "<tr>",
                    f"<td>{_html(item.evidence_id)}</td>",
                    f"<td>{_html(item.subject)}</td>",
                    f"<td>{_html(item.claim)}</td>",
                    f"<td>{_html(_value_text(item.value))}</td>",
                    f"<td>{_html(item.source)}</td>",
                    f"<td>{item.confidence:.2f}</td>",
                    "</tr>",
                )
            )
            for item in explorer.evidence
        ]
    if not rows:
        rows.append('<tr><td colspan="6">No evidence</td></tr>')
    return _section(
        "Session Evidence",
        _table(("Evidence", "Subject", "Claim", "Value", "Source", "Confidence"), tuple(rows)),
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


def _section(title: str, body: str) -> str:
    return "\n".join(
        (
            '<section class="panel">',
            f"<h2>{_html(title)}</h2>",
            body,
            "</section>",
        )
    )


def _metric_card(label: str, value: object) -> str:
    return "\n".join(
        (
            '<article class="card">',
            f"<span>{_html(label)}</span>",
            f"<strong>{_html(value)}</strong>",
            "</article>",
        )
    )


def _table(headers: tuple[str, ...], rows: tuple[str, ...]) -> str:
    header = "".join(f"<th>{_html(item)}</th>" for item in headers)
    return (
        '<div class="table-wrap"><table><thead><tr>'
        + header
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _action_count(snapshot: WebConsoleSnapshot) -> str:
    return f"{snapshot.metrics.action_started_count}/{snapshot.metrics.action_completed_count}"


def _ready_text(snapshot: WebConsoleSnapshot) -> str:
    if snapshot.ready.ready:
        return "yes"
    return "no: " + snapshot.ready.reason


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


def _profile_domain_text(profile: ProfileView) -> str:
    if not profile.domains:
        return "none"
    return ", ".join(f"{identity.name}@{identity.version}" for identity in profile.domains)


def _enum_tuple_text(values: tuple[Any, ...]) -> str:
    if not values:
        return "none"
    return ", ".join(str(getattr(value, "value", value)) for value in values)


def _value_text(value: object) -> str:
    if isinstance(value, dict | list):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _html(value: object) -> str:
    return escape(str(value), quote=False)


def _attr(value: object) -> str:
    return escape(str(value), quote=True)


def _stylesheet() -> str:
    return """
:root {
  color-scheme: light;
  font-family:
    Inter,
    ui-sans-serif,
    system-ui,
    -apple-system,
    BlinkMacSystemFont,
    "Segoe UI",
    sans-serif;
  background: #f5f7fb;
  color: #172033;
}
* {
  box-sizing: border-box;
}
body {
  margin: 0;
}
.shell {
  width: min(1180px, calc(100vw - 40px));
  margin: 0 auto;
  padding: 28px 0 40px;
}
.hero, .panel, .card {
  background: #ffffff;
  border: 1px solid #d9e1ec;
  border-radius: 8px;
  box-shadow: 0 1px 2px rgba(23, 32, 51, 0.05);
}
.hero {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  align-items: flex-start;
  padding: 24px;
  border-top: 4px solid #0f766e;
}
.hero p, .hero h1 {
  margin: 0;
}
.hero p {
  color: #5d6b82;
  font-size: 13px;
  font-weight: 700;
  text-transform: uppercase;
}
.hero h1 {
  margin-top: 4px;
  font-size: 30px;
  line-height: 1.1;
}
.hero span {
  display: inline-block;
  margin-top: 10px;
  color: #5d6b82;
  font-size: 14px;
}
.status {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
.pill {
  border-radius: 999px;
  padding: 7px 10px;
  font-size: 13px;
  font-weight: 700;
}
.ok {
  background: #dcfce7;
  color: #166534;
}
.warn {
  background: #fff7ed;
  color: #9a3412;
}
.grid {
  display: grid;
  gap: 12px;
}
.cards {
  grid-template-columns: repeat(6, minmax(0, 1fr));
  margin: 16px 0;
}
.card {
  min-height: 82px;
  padding: 16px;
}
.card span {
  display: block;
  color: #5d6b82;
  font-size: 13px;
}
.card strong {
  display: block;
  margin-top: 8px;
  font-size: 24px;
}
.panel {
  margin-top: 16px;
  padding: 20px;
}
.panel h2 {
  margin: 0 0 14px;
  font-size: 18px;
}
.table-wrap {
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th, td {
  border-bottom: 1px solid #e6ebf2;
  padding: 10px 8px;
  text-align: left;
  vertical-align: top;
  font-size: 13px;
}
th {
  color: #5d6b82;
  font-size: 12px;
  text-transform: uppercase;
}
a {
  color: #0f766e;
  font-weight: 700;
  text-decoration: none;
}
.details {
  display: grid;
  grid-template-columns: 170px minmax(0, 1fr);
  gap: 10px 14px;
  margin: 0;
}
.details dt {
  color: #5d6b82;
  font-weight: 700;
}
.details dd {
  margin: 0;
}
.empty {
  color: #5d6b82;
  margin: 0;
}
@media (max-width: 860px) {
  .shell {
    width: min(100vw - 24px, 1180px);
    padding-top: 16px;
  }
  .hero {
    display: block;
  }
  .status {
    justify-content: flex-start;
    margin-top: 16px;
  }
  .cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .details {
    grid-template-columns: 1fr;
  }
}
""".strip()


__all__ = ["WebConsoleSnapshot", "build_web_console_snapshot", "render_web_console"]
