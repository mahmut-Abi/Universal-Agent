"""Human-readable text projections for the Golden Path CLI commands.

The renderers consume the same JSON-safe bodies that the CLI's machine output
uses, so the local in-process path and the agentd thin-client path render
identical text without owning a second projection layer.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

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
        lines.extend(_confirmation_banner_lines(pending_action, session_id, reason))
    elif status == "waiting":
        lines.append("Next: agent session resume " + session_id)
    if status == "failed":
        lines.append("Next: agent doctor")
    return "\n".join(lines) + "\n"


def _confirmation_banner_lines(
    pending_action: JsonMapping,
    session_id: str,
    reason: str,
) -> list[str]:
    capability = str(pending_action.get("capability") or "action")
    target = str(pending_action.get("target") or "target")
    arguments = pending_action.get("arguments")
    argument_map = arguments if isinstance(arguments, dict) else {}
    before = _first_present(argument_map, ("current_replicas", "previous_replicas"))
    after = _first_present(argument_map, ("replicas", "desired_replicas"))
    lines = [
        "Confirmation Required",
        f"Pending: {capability}",
        f"Target: {target}",
    ]
    if before or after:
        lines.append(f"Before/after: {before or '?'} -> {after or '?'}")
    lines.extend(
        [
            f"Reason: {reason or 'runtime policy requires explicit confirmation'}",
            "Risk: guarded mutation; Runtime policy paused before execution",
            f"Resume: agent session resume {session_id} --confirmed true",
        ]
    )
    return lines


def _first_present(values: JsonMapping, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = values.get(key)
        if value is not None:
            return str(value)
    return ""


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
    raw_status = str(session_body.get("goal_status", ""))
    goal_status = STATUS_LABELS.get(raw_status, raw_status)
    goal = str(session_body.get("goal_description") or "")
    task = str(session_body.get("current_task_description") or "")
    task_status = str(session_body.get("current_task_status") or "")
    domain = f"{session_body.get('domain_name', '')}@{session_body.get('domain_version', '')}"
    evidence_count = _count_event_types(events_body, "EvidenceRecorded")
    action_count = _count_event_types(events_body, "ActionStarted")
    termination = str(session_body.get("termination_reason") or "")
    lines = [
        "Summary",
        f"  Session: {session_id}",
        f"  Status: {goal_status}",
        f"  Goal: {goal}",
        f"  Task: {task} ({task_status})",
        f"  Domains: {domain}",
        f"  Evidence: {evidence_count}",
        f"  Actions: {action_count}",
    ]
    if termination:
        lines.append(f"  Terminal reason: {termination}")
    pending = session_body.get("pending_action")
    if isinstance(pending, dict):
        lines.extend(
            f"  {line}" for line in _confirmation_banner_lines(pending, session_id, termination)
        )
    lines.extend(["", "What happened"])
    if raw_status == "completed":
        summary = termination or "success criteria satisfied"
        lines.append(f"  The Agent completed the goal: {summary}.")
    elif raw_status == "waiting":
        lines.append("  The Agent stopped at a safe waiting point before continuing.")
    elif raw_status == "failed":
        lines.append(f"  The Agent failed: {termination or 'see session explain for details'}.")
    elif raw_status == "cancelled":
        lines.append("  The session was cancelled before completion.")
    else:
        lines.append("  The session is still in progress or has an unknown status.")
    if events:
        lines.append("  Raw timeline: agent session events " + session_id)
    if isinstance(pending, dict):
        pass
    elif raw_status in {"failed", "waiting"}:
        lines.append(f"  Next: agent session explain {session_id}")
    return "\n".join(lines) + "\n"


def render_session_explain_text(
    session_body: JsonMapping,
    events_body: JsonMapping | None = None,
) -> str:
    session_id = str(session_body.get("session_id", ""))
    raw_status = str(session_body.get("goal_status", ""))
    pending = session_body.get("pending_action")
    termination = str(session_body.get("termination_reason") or "")
    events = [item for item in _events_of(events_body) if isinstance(item, dict)]
    reason, fix = _explain_session_reason(raw_status, termination, pending, events)
    return (
        "\n".join(
            [
                "Error",
                f"  {reason[0]}",
                "Reason",
                f"  {reason[1]}",
                "Try",
                f"  {fix}",
                f"Session: {session_id}",
            ]
        )
        + "\n"
    )


def render_session_not_found_explain_text(session_id: str) -> str:
    return (
        "\n".join(
            [
                "Error",
                "  Session not found",
                "Reason",
                f"  No persisted session exists with id {session_id} in the active store.",
                "Try",
                (
                    "  Run `agent session list` or pass the same --profile-config used for "
                    "`agent run`."
                ),
                f"Session: {session_id}",
            ]
        )
        + "\n"
    )


def _explain_session_reason(
    raw_status: str,
    termination: str,
    pending: JsonValue,
    events: list[dict[str, JsonValue]],
) -> tuple[tuple[str, str], str]:
    lower = termination.lower()
    if isinstance(pending, dict) or "confirmation" in lower or raw_status == "waiting":
        return (
            ("Confirmation required", "Runtime policy paused before a guarded action."),
            "Review the pending action, then run `agent session resume <id> --confirmed true`.",
        )
    if raw_status not in {"failed", "waiting"}:
        return (
            (
                "Session is not waiting",
                "This session has no failure or pending confirmation to explain.",
            ),
            "Use `agent session show <id>` or `agent session events <id>` for details.",
        )
    event_text = " ".join(str(event.get("data", "")) for event in events).lower() + " " + lower
    if "api key" in event_text or "credential" in event_text or "secret" in event_text:
        return (
            (
                "Missing model credentials",
                "The configured model needs a secret that is unavailable.",
            ),
            "Set the required environment variable or re-run `agent init` with a scripted profile.",
        )
    if "invalid finish" in event_text or "finish decision" in event_text:
        return (
            (
                "Invalid finish decision",
                "The model proposed a finish decision the Runtime rejected.",
            ),
            "Inspect `agent session events <id>` and retry with a stricter model/profile config.",
        )
    evaluation_incomplete = "incomplete" in event_text or "did not complete" in event_text
    if "evaluation" in event_text and evaluation_incomplete:
        return (
            ("Evaluator did not complete", "Tool output did not satisfy the Runtime evaluator."),
            "Inspect `agent session evidence <id>` and adjust the goal success criteria.",
        )
    if "tool" in event_text or "domain" in event_text or "actioncompleted" in event_text:
        return (
            ("Domain/tool failure", "A domain capability or tool failed during execution."),
            "Inspect `agent session events <id>` and run `agent doctor` to verify dependencies.",
        )
    return (
        ("Session needs attention", termination or "The Runtime stopped before completion."),
        "Run `agent session events <id>` for the timeline, then `agent doctor`.",
    )


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


def render_profile_show_text(
    profile_body: JsonMapping,
    *,
    runtime_body: JsonMapping | None = None,
    policies_body: JsonMapping | None = None,
) -> str:
    runtime = runtime_body if isinstance(runtime_body, dict) else {}
    model = runtime.get("model")
    model_map = model if isinstance(model, dict) else {}
    store = runtime.get("store")
    store_map = store if isinstance(store, dict) else {}
    limits = runtime.get("limits")
    limits_map = limits if isinstance(limits, dict) else {}
    policies = policies_body.get("policies") if isinstance(policies_body, dict) else None
    lines = [
        f"Profile: {profile_body.get('name', '')}",
        f"Version: {profile_body.get('version', '')}",
    ]
    description = str(profile_body.get("description") or "")
    if description:
        lines.append(f"Description: {description}")
    if model_map:
        lines.extend(
            [
                "",
                "Model",
                f"  Provider: {model_map.get('provider', '')}",
                f"  Model: {model_map.get('name', '')}",
            ]
        )
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
    lines.append("Domains")
    lines.extend(f"  {name}" for name in domain_names if name)
    lines.append("")
    lines.append("Policy")
    if isinstance(policies, list) and policies:
        lines.extend(
            f"  {item.get('name', '')}: {item.get('effect', item.get('policy_type', ''))}"
            for item in policies
            if isinstance(item, dict)
        )
    else:
        lines.append("  safe runtime policy")
    if runtime:
        store_path = _resolved_store_path(store_map.get("path"))
        lines.extend(
            [
                "",
                "Runtime",
                f"  Max steps: {limits_map.get('max_iterations', '')}",
                f"  Store: {store_map.get('backend', '')}"
                + (f" at {store_path}" if store_path else ""),
            ]
        )
    return "\n".join(lines) + "\n"


def render_config_text(
    config_body: JsonMapping,
    *,
    profile_config_path: str | None = None,
    config_dir: str | None = None,
    active_profile: str | None = None,
    policies_body: JsonMapping | None = None,
) -> str:
    model = config_body.get("model")
    model_map = model if isinstance(model, dict) else {}
    store = config_body.get("store")
    store_map = store if isinstance(store, dict) else {}
    limits = config_body.get("limits")
    limits_map = limits if isinstance(limits, dict) else {}
    domains = config_body.get("domains")
    secrets = config_body.get("secrets")
    policies = policies_body.get("policies") if isinstance(policies_body, dict) else None
    store_path = _resolved_store_path(store_map.get("path"))

    lines = [
        "Universal Agent Configuration",
        "",
        "Agent",
        f"  Active profile: {active_profile or '(default discovery)'}",
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
            + (f" at {store_path}" if store_path else ""),
        ]
    )
    lines.extend(["", "Policy"])
    if isinstance(policies, list) and policies:
        for item in policies:
            if not isinstance(item, dict):
                continue
            effect = item.get("effect") or item.get("policy_type") or "policy"
            lines.append(f"  {item.get('name', '')}: {effect}")
    else:
        lines.append("  safe runtime policy")
    lines.extend(["", "Domains"])
    if isinstance(domains, list):
        for item in domains:
            if not isinstance(item, dict):
                continue
            backend = f" ({item['backend']})" if item.get("backend") else ""
            primary = "*" if item.get("primary") else " "
            lines.append(f"  {primary} {item.get('name', '')}@{item.get('version', '')}{backend}")
    lines.extend(["", "Credential status"])
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
            f"  Profile config: {profile_config_path or '(in-memory default; run `agent init`)'}",
            *([f"  Settings: {config_dir}/config.json"] if config_dir else []),
            "",
            "Discovery order:",
            "  1. --profile-config",
            "  2. $AGENT_CONFIG_DIR/profile.json",
            "  3. ./universal-agent/profile.json",
            "  4. ~/.universal-agent/profile.json",
        ]
    )
    return "\n".join(lines) + "\n"


def _resolved_store_path(value: object) -> str:
    if not value:
        return ""
    return str(Path(str(value)).expanduser().resolve())
