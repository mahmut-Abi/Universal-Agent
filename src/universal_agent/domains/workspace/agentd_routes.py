"""Workspace Domain contribution to the agentd HTTP surface.

Exposes the workspace file-operation flow over the Runtime API:
run a goal against the sandboxed workspace domain and inspect the
evidence recorded for a session. The payloads are plain JSON so any
client (web console, remote thin client) can drive the domain without
the CLI.

The agentd host discovers this module through the
``universal_agent.agentd_routes`` entry-point group; it never imports a
concrete domain, and this module never imports the agentd adapter.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from universal_agent.core import (
    Goal,
    JsonMapping,
    JsonValue,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.host_contracts import (
    DomainRouteContribution,
    DomainRouteDefinition,
    DomainRouteResponse,
    domain_bad_request,
    domain_json_response,
    domain_method_not_allowed,
    match_domain_route,
)
from universal_agent.service import RuntimeService

_WORKSPACE_ROUTE_DEFINITIONS = (
    DomainRouteDefinition("workspace_run", "/v1/workspace/run", ("POST",)),
    DomainRouteDefinition("workspace_evidence", "/v1/workspace/evidence", ("POST",)),
)

_WORKSPACE_OPENAPI_METADATA: dict[str, tuple[str, str, str]] = {
    "workspace_run": (
        "Workspace run",
        "Run one goal against the sandboxed workspace domain.",
        "Workspace",
    ),
    "workspace_evidence": (
        "Workspace evidence",
        "Return the evidence claims recorded for a workspace session.",
        "Workspace",
    ),
}


def _text(body: JsonMapping, key: str) -> str | None:
    value = body.get(key)
    return value if isinstance(value, str) and value else None


def _criteria(body: JsonMapping) -> tuple[SuccessCriterion, ...]:
    raw = body.get("criteria")
    if not isinstance(raw, list):
        return ()
    criteria: list[SuccessCriterion] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        raw_key = item.get("key")
        if not isinstance(raw_key, str):
            continue
        key = raw_key
        expected: JsonValue = item.get("expected", True)
        criteria.append(SuccessCriterion(key, expected))
    return tuple(criteria)


def _task_criteria(body: JsonMapping) -> tuple[str, ...]:
    raw = body.get("task_criteria")
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw if isinstance(item, str) and item)


def _evidence_claims(events: Iterable[object]) -> JsonValue:
    claims: list[dict[str, JsonValue]] = []
    for event in events:
        if getattr(event, "type", None) != "EvidenceRecorded":
            continue
        data = getattr(event, "data", {})
        if not hasattr(data, "get"):
            continue
        claims.append(
            {
                "subject": str(data.get("subject", "")),
                "claim": str(data.get("claim", "")),
                "value": data.get("value"),
            }
        )
    return cast(JsonValue, claims)


async def _handle_run(
    service: RuntimeService,
    body: JsonMapping,
) -> DomainRouteResponse:
    goal_text = _text(body, "goal")
    if goal_text is None:
        return domain_bad_request("goal is required")
    run = await service.run_goal(
        Goal(goal_text, _criteria(body)),
        Task(_text(body, "task") or goal_text, _task_criteria(body)),
    )
    result = run.result
    events = await service.list_events(result.session_id)
    return domain_json_response(
        immutable_json(
            {
                "status": result.status.value,
                "session_id": str(result.session_id),
                "iterations": result.iterations,
                "reason": result.reason,
                "error_code": result.error_code.value if result.error_code else None,
                "evidence": _evidence_claims(events),
            }
        )
    )


async def _handle_evidence(
    service: RuntimeService,
    body: JsonMapping,
) -> DomainRouteResponse:
    session_id = _text(body, "session_id")
    if session_id is None:
        return domain_bad_request("session_id is required")
    from universal_agent.core import SessionId

    events = await service.list_events(SessionId(session_id))
    return domain_json_response(immutable_json({"evidence": _evidence_claims(events)}))


async def handle_workspace_route(
    service: RuntimeService,
    method: str,
    path: str,
    body: JsonMapping,
) -> DomainRouteResponse | None:
    match = match_domain_route(_WORKSPACE_ROUTE_DEFINITIONS, method, path)
    if match is None:
        return None
    route, method_allowed = match
    if not method_allowed:
        return domain_method_not_allowed(route.methods)
    if route.name == "workspace_run":
        return await _handle_run(service, body)
    return await _handle_evidence(service, body)


def workspace_agentd_contribution() -> DomainRouteContribution:
    """Entry-point factory for the workspace agentd route contribution."""

    return DomainRouteContribution(
        domain="workspace",
        route_definitions=_WORKSPACE_ROUTE_DEFINITIONS,
        handle=handle_workspace_route,
        openapi_metadata=_WORKSPACE_OPENAPI_METADATA,
        openapi_tags=(("Workspace", "Sandboxed file-operation domain"),),
    )


__all__ = ["workspace_agentd_contribution"]
