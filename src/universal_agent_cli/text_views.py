"""Human-readable text projections for the Golden Path CLI commands.

The renderers consume the same JSON-safe bodies that the CLI's machine output
uses, so the local in-process path and the agentd thin-client path render
identical text without owning a second projection layer.
"""

from __future__ import annotations

from datetime import datetime

from universal_agent.core import JsonMapping, JsonValue

STATUS_LABELS = {
    "completed": "success",
    "waiting": "waiting",
    "cancelled": "cancelled",
    "failed": "failed",
}


def _events_of(events_body: JsonMapping | None) -> list[JsonValue]:
    if not events_body:
        return []
    events = events_body.get("events")
    if isinstance(events, list):
        return events
    return []


def _count_event_types(events_body: JsonMapping | None, event_type: str) -> int:
    return sum(
        1
        for item in _events_of(events_body)
        if isinstance(item, dict) and item.get("type") == event_type
    )


def _format_duration(seconds: float) -> str:
    if seconds >= 10:
        return f"{seconds:.1f}s"
    return f"{seconds:.2f}s"


def _short_time(value: str) -> str:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def render_run_text(
    run_body: JsonMapping,
    *,
    duration_seconds: float,
    events_body: JsonMapping | None = None,
    profile: str | None = None,
) -> str:
    result = run_body.get("result")
    session = run_body.get("session")
    if not isinstance(result, dict) or not isinstance(session, dict):
        return ""
    status = str(result.get("status", ""))
    session_id = str(result.get("session_id", ""))
    reason = str(result.get("reason") or "")
    goal = str(session.get("goal_description") or "")
    pending_action = session.get("pending_action")

    lines = [
        "Agent started",
        f"Session: {session_id}",
    ]
    if profile:
        lines.append(f"Profile: {profile}")
    lines.append(f"Goal: {goal}")
    lines.append("")
    lines.append("Agent completed")
    lines.append("")
    lines.append(f"Status: {STATUS_LABELS.get(status, status)}")
    lines.append(f"Session: {session_id}")
    lines.append(f"Duration: {_format_duration(duration_seconds)}")
    lines.append(f"Steps: {result.get('iterations', 0)}")
    lines.append(f"Tool calls: {_count_event_types(events_body, 'ActionStarted')}")
    lines.append(f"Evidence: {_count_event_types(events_body, 'EvidenceRecorded')}")
    if status == "failed":
        lines.append(f"Reason: {reason}")
    if status == "waiting" and isinstance(pending_action, dict):
        capability = str(pending_action.get("capability") or "action")
        lines.append(f"Pending: {capability} requires human confirmation")
        lines.append(f"Next: agent session resume {session_id} --confirmed true")
    elif status == "waiting":
        lines.append("Next: agent session resume " + session_id)
    if status == "failed":
        lines.append("Next: agent doctor")
    return "\n".join(lines) + "\n"


def render_session_list_text(batch_body: JsonMapping) -> str:
    sessions = batch_body.get("sessions")
    rows: list[tuple[str, str, str, str]] = []
    if isinstance(sessions, list):
        for item in sessions:
            if not isinstance(item, dict):
                continue
            rows.append(
                (
                    str(item.get("session_id", "")),
                    STATUS_LABELS.get(
                        str(item.get("goal_status", "")), str(item.get("goal_status", ""))
                    ),
                    _short_time(str(item.get("created_at", ""))),
                    str(item.get("goal_description", "")),
                )
            )
    width = max([len(row[0]) for row in rows] + [len("SESSION")])
    status_width = max([len(row[1]) for row in rows] + [len("STATUS")])
    lines = [
        f"{'SESSION':<{width}}  {'STATUS':<{status_width}}  {'CREATED':<19}  GOAL",
    ]
    for session_id, status, created, goal in rows:
        lines.append(f"{session_id:<{width}}  {status:<{status_width}}  {created:<19}  {goal}")
    if not rows:
        lines.append('(no sessions yet — run `agent run "your goal"`)')
    return "\n".join(lines) + "\n"


def render_session_show_text(
    session_body: JsonMapping,
    events_body: JsonMapping | None = None,
) -> str:
    events = [item for item in _events_of(events_body) if isinstance(item, dict)]
    session_id = str(session_body.get("session_id", ""))
    goal_status = STATUS_LABELS.get(
        str(session_body.get("goal_status", "")),
        str(session_body.get("goal_status", "")),
    )
    lines = [
        f"Session: {session_id}",
        f"Status: {goal_status}",
        f"Goal: {session_body.get('goal_description', '')}",
        (
            f"Task: {session_body.get('current_task_description', '')} "
            f"({session_body.get('current_task_status', '')})"
        ),
        f"Domains: {session_body.get('domain_name', '')}@{session_body.get('domain_version', '')}",
        "",
        "Timeline:",
    ]
    for event in events:
        occurred = _short_time(str(event.get("occurred_at", "")))
        event_type = str(event.get("type", ""))
        detail = _event_detail(event)
        lines.append(f"{occurred}  {event_type}{detail}")
    lines.append("")
    lines.append(f"Evidence: {_count_event_types(events_body, 'EvidenceRecorded')}")
    lines.append(f"Actions: {_count_event_types(events_body, 'ActionStarted')}")
    pending = session_body.get("pending_action")
    if isinstance(pending, dict):
        capability = str(pending.get("capability") or "action")
        lines.append(f"Pending: {capability} requires human confirmation")
        lines.append(f"Next: agent session resume {session_id} --confirmed true")
    if str(session_body.get("termination_reason") or ""):
        lines.append(f"Termination: {session_body.get('termination_reason')}")
    return "\n".join(lines) + "\n"


def _event_detail(event: JsonMapping) -> str:
    data = event.get("data")
    if not isinstance(data, dict):
        return ""
    parts: list[str] = []
    for key in ("capability", "target", "reason", "status"):
        value = data.get(key)
        if value:
            parts.append(f"{key}={value}")
    if not parts:
        return ""
    return " " + " ".join(parts)


def render_profile_list_text(profiles_body: JsonMapping) -> str:
    profiles = profiles_body.get("profiles")
    names: list[str] = []
    if isinstance(profiles, list):
        for item in profiles:
            if isinstance(item, dict):
                names.append(str(item.get("name", "")))
            else:
                names.append(str(item))
    lines = [name for name in names if name]
    if not lines:
        lines.append("(no profiles)")
    return "\n".join(lines) + "\n"


def render_profile_show_text(profile_body: JsonMapping) -> str:
    lines = [
        f"Profile: {profile_body.get('name', '')}",
        f"Version: {profile_body.get('version', '')}",
    ]
    description = str(profile_body.get("description") or "")
    if description:
        lines.append(f"Description: {description}")
    domains = profile_body.get("domains")
    if not isinstance(domains, list) or not domains:
        domains = (
            [
                {
                    "name": profile_body.get("domain_name", ""),
                    "version": profile_body.get("domain_version", ""),
                }
            ]
            if profile_body.get("domain_name")
            else []
        )
    domain_names = [
        f"{item.get('name', '')}@{item.get('version', '')}"
        for item in domains
        if isinstance(item, dict)
    ]
    lines.append("")
    lines.append("Domains:")
    lines.extend(f"  {name}" for name in domain_names if name)
    return "\n".join(lines) + "\n"


def render_config_text(
    config_body: JsonMapping,
    *,
    profile_config_path: str | None = None,
    config_dir: str | None = None,
) -> str:
    model = config_body.get("model")
    model_map = model if isinstance(model, dict) else {}
    store = config_body.get("store")
    store_map = store if isinstance(store, dict) else {}
    limits = config_body.get("limits")
    limits_map = limits if isinstance(limits, dict) else {}
    domains = config_body.get("domains")
    secrets = config_body.get("secrets")

    lines = [
        "Universal Agent Configuration",
        "",
        "Model",
        f"  Provider: {model_map.get('provider', '')}",
        f"  Model: {model_map.get('name', '')}",
    ]
    if model_map.get("endpoint"):
        lines.append(f"  Endpoint: {model_map.get('endpoint')}")
    if model_map.get("api_key_secret"):
        lines.append(f"  API key secret: {model_map.get('api_key_secret')}")
    lines.extend(
        [
            "",
            "Runtime",
            f"  Max steps: {limits_map.get('max_iterations', '')}",
            f"  Store: {store_map.get('backend', '')}"
            + (f" at {store_map.get('path')}" if store_map.get("path") else ""),
        ]
    )
    lines.extend(["", "Domains"])
    if isinstance(domains, list):
        for item in domains:
            if not isinstance(item, dict):
                continue
            backend = f" ({item['backend']})" if item.get("backend") else ""
            primary = "*" if item.get("primary") else " "
            lines.append(f"  {primary} {item.get('name', '')}@{item.get('version', '')}{backend}")
    lines.extend(["", "Secrets"])
    if isinstance(secrets, list) and secrets:
        for item in secrets:
            if not isinstance(item, dict):
                continue
            available = item.get("available")
            state = "configured" if available else "missing"
            if available is None:
                state = "unknown"
            lines.append(f"  {item.get('key', '')}: {state}")
    else:
        lines.append("  (none declared)")
    lines.extend(
        [
            "",
            "Config:",
            f"  {profile_config_path or '(in-memory default; run `agent init`)'}",
            *([f"  {config_dir}/config.json"] if config_dir else []),
        ]
    )
    return "\n".join(lines) + "\n"
