"""Web Console routes: JSON operator actions plus a built-in info page.

The interactive Web Console frontend is the standalone ``web/`` Node.js
service (deployed separately); agentd only serves the JSON projections and
operator actions under ``/console`` plus a built-in info page. The operator
actions dispatch through the same RuntimeService methods the CLI and agentd
use, so policy checks and the pending-action confirmation boundary stay
identical across surfaces.
"""

from __future__ import annotations

from typing import Any

from universal_agent.agentd.http import (
    HttpResponse,
    json_response,
    method_not_allowed,
    not_found,
    text_response,
)
from universal_agent.agentd.representations import runtime_run_body
from universal_agent.agentd.routing import (
    AgentdRouteDefinition,
    AgentdRouteMatch,
    AgentdRouteMatcher,
)
from universal_agent.core import SessionId, to_json_object
from universal_agent.evaluation.console import (
    build_evaluation_console_snapshot,
)
from universal_agent.service import RuntimeService
from universal_agent.service.evidence_drilldown import evidence_drilldown_body
from universal_agent.service.timeline import timeline_body
from universal_agent.service.world_explorer import world_explorer_body
from universal_agent.state import StateNotFoundError

_CONSOLE_ROUTES = AgentdRouteMatcher(
    (
        AgentdRouteDefinition("console_root", "/"),
        AgentdRouteDefinition("console_root", "/console"),
        AgentdRouteDefinition("console_asset", "/console/app.js"),
        AgentdRouteDefinition("console_asset", "/console/style.css"),
        AgentdRouteDefinition("console_settings", "/console/settings"),
        AgentdRouteDefinition("console_profiles", "/console/profiles"),
        AgentdRouteDefinition("console_evaluations", "/console/evaluations"),
        AgentdRouteDefinition("console_doctor", "/console/doctor"),
        AgentdRouteDefinition("console_distributed", "/console/distributed"),
        AgentdRouteDefinition("console_multi_agent", "/console/multi-agent"),
        AgentdRouteDefinition("console_evidence", "/console/evidence"),
        AgentdRouteDefinition("console_world", "/console/world"),
        AgentdRouteDefinition(
            "console_domain_package_version",
            "/console/domain-packages/{name}/{version}",
        ),
        AgentdRouteDefinition("console_domain_package", "/console/domain-packages/{name}"),
        AgentdRouteDefinition("console_domain_version", "/console/domains/{name}/{version}"),
        AgentdRouteDefinition("console_domain", "/console/domains/{name}"),
        AgentdRouteDefinition("console_sessions", "/console/sessions"),
        AgentdRouteDefinition(
            "console_session_timeline",
            "/console/sessions/{session_id}/timeline",
        ),
        AgentdRouteDefinition(
            "console_session_world_explorer",
            "/console/sessions/{session_id}/world-explorer",
        ),
        AgentdRouteDefinition(
            "console_session_evidence_drilldown",
            "/console/sessions/{session_id}/evidence-drilldown",
        ),
        AgentdRouteDefinition(
            "console_session_pause",
            "/console/sessions/{session_id}/pause",
            methods=("POST",),
        ),
        AgentdRouteDefinition(
            "console_session_resume",
            "/console/sessions/{session_id}/resume",
            methods=("POST",),
        ),
        AgentdRouteDefinition(
            "console_session_cancel",
            "/console/sessions/{session_id}/cancel",
            methods=("POST",),
        ),
        AgentdRouteDefinition("console_session_suffix", "/console/sessions/{session_id}/{suffix}"),
        AgentdRouteDefinition("console_session", "/console/sessions/{session_id}"),
    ),
)

_CONSOLE_SESSION_ACTION_ROUTES = {
    "console_session_pause",
    "console_session_resume",
    "console_session_cancel",
}

_CONFIRMED_FORM_VALUES = {"true": True, "false": False}

_CONSOLE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Universal Agent Runtime API</title></head>
<body style="font-family: system-ui, sans-serif; margin: 3rem;">
<h1>Universal Agent Runtime API</h1>
<p>This agentd process serves the Runtime API and JSON projections.
The interactive multi-session chat UI is the standalone
<code>universal-agent-web</code> Node.js service (see the repository
<code>web/</code> directory and Docker Compose <code>web</code> service).</p>
<p>The Runtime API remains fully available: see <a href="/openapi.json">/openapi.json</a>.</p>
</body></html>"""


def _html_response(body: str, status_code: int = 200) -> HttpResponse:
    return text_response(body, content_type="text/html; charset=utf-8", status_code=status_code)


def _asset_response(filename: str) -> HttpResponse:
    """Legacy asset path: the frontend package was replaced by the standalone
    web service; serve the built-in console info page instead."""
    del filename
    return _html_response(_CONSOLE_PAGE)


def _index_response() -> HttpResponse:
    return _html_response(_CONSOLE_PAGE)


def _json_run_response(run: Any) -> HttpResponse:
    body = to_json_object({"run": runtime_run_body(run)}, fallback_to_string=True)
    return json_response(body)


async def _handle_console_session_action(
    service: RuntimeService,
    route: AgentdRouteMatch,
    request: Any,
) -> HttpResponse:
    """Dispatch a console operator action through the same RuntimeService
    methods the CLI and agentd use, so policy checks and the pending-action
    confirmation boundary stay identical across surfaces."""

    session_id = SessionId(route.path_params["session_id"])
    reason = _form_text(request.body, "reason")
    confirmed_value = request.body.get("confirmed")
    confirmed: bool | None
    if isinstance(confirmed_value, bool):
        confirmed = confirmed_value
    elif isinstance(confirmed_value, str):
        confirmed = _CONFIRMED_FORM_VALUES.get(confirmed_value)
    else:
        confirmed = None
    try:
        if route.name == "console_session_pause":
            run = await service.pause_session(
                session_id,
                reason=reason or "session paused",
            )
        elif route.name == "console_session_resume":
            run = await service.resume_session(session_id, confirmed=confirmed)
        else:
            run = await service.cancel_session(
                session_id,
                reason=reason or "session cancelled",
            )
    except StateNotFoundError:
        return not_found(f"session not found: {session_id}")
    return _json_run_response(run)


def _form_text(body: Any, key: str) -> str | None:
    value = body.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _evaluations_payload(
    service: RuntimeService, evaluation_report_dir: str | None
) -> dict[str, Any]:
    if evaluation_report_dir is None:
        return {"status": "not_configured", "report_dir": None, "reports": []}
    snapshot = build_evaluation_console_snapshot(evaluation_report_dir)
    reports = [
        {
            "suite_name": report.suite_name,
            "passed": report.passed,
            "scenario_count": report.summary.scenario_count,
            "passed_count": report.summary.passed_count,
            "failed_count": report.summary.failed_count,
            "gate_passed": None if report.gate is None else report.gate.passed,
            "failed_scenarios": [
                scenario.scenario_name for scenario in report.scenarios if not scenario.passed
            ],
        }
        for report in snapshot.reports
    ]
    return {"status": "ok", "report_dir": str(snapshot.report_dir), "reports": reports}


async def handle_console_route(
    service: RuntimeService,
    evaluation_report_dir: str | None,
    request: Any,
    method: str,
    path: str,
) -> HttpResponse | None:
    route = _CONSOLE_ROUTES.match(path, method)
    if route is None:
        return None

    if not route.method_allowed:
        return method_not_allowed(route.allowed_methods)

    if route.name in _CONSOLE_SESSION_ACTION_ROUTES:
        return await _handle_console_session_action(service, route, request)

    if route.name == "console_asset":
        filename = path.rsplit("/", 1)[-1]
        return _asset_response(filename)

    if route.name == "console_evaluations":
        return json_response(_evaluations_payload(service, evaluation_report_dir))

    if route.name == "console_session_timeline":
        session_id = SessionId(route.path_params["session_id"])
        try:
            batch = await service.stream_events(session_id)
        except StateNotFoundError as exc:
            return not_found(str(exc))
        return json_response(timeline_body(batch.events))

    if route.name == "console_session_world_explorer":
        session_id = SessionId(route.path_params["session_id"])
        try:
            explorer = await service.session_explorer(session_id)
            world = await service.session_world(session_id)
        except StateNotFoundError as exc:
            return not_found(str(exc))
        return json_response(world_explorer_body(world, explorer.evidence))

    if route.name == "console_session_evidence_drilldown":
        session_id = SessionId(route.path_params["session_id"])
        try:
            explorer = await service.session_explorer(session_id)
        except StateNotFoundError as exc:
            return not_found(str(exc))
        return json_response(evidence_drilldown_body(session_id, explorer.evidence))

    if route.name in {
        "console_root",
        "console_settings",
        "console_profiles",
        "console_doctor",
        "console_distributed",
        "console_multi_agent",
        "console_evidence",
        "console_world",
        "console_domain_package_version",
        "console_domain_package",
        "console_domain_version",
        "console_domain",
        "console_sessions",
        "console_session_suffix",
        "console_session",
    }:
        # Single-page frontend: every view is a client-side hash route over
        # the Runtime API, so all console GET paths serve the shell page.
        return _index_response()

    return not_found(f"unknown console route: {path}")
