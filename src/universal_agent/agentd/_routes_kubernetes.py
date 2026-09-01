"""HTTP routes for the Kubernetes operator commands.

These routes expose the kubernetes operator flow (preflight, model probe,
check, run, evidence) over the Runtime API so clients no longer need the
kernel's CLI dispatch for remote operation. The payloads mirror the CLI
output bodies exactly.
"""

from __future__ import annotations

import argparse

from universal_agent.agentd.http import (
    HttpRequest,
    HttpResponse,
    bad_request,
    json_response,
    method_not_allowed,
)
from universal_agent.agentd.routing import (
    AgentdRouteDefinition,
    AgentdRouteMatch,
    AgentdRouteMatcher,
)
from universal_agent.core import JsonMapping, immutable_json
from universal_agent.domains.kubernetes.cli_reports import dispatch_kubernetes
from universal_agent.service import RuntimeService

_KUBERNETES_ROUTE_DEFINITIONS = (
    AgentdRouteDefinition("kubernetes_preflight", "/v1/kubernetes/preflight", ("POST",)),
    AgentdRouteDefinition("kubernetes_model_probe", "/v1/kubernetes/model-probe", ("POST",)),
    AgentdRouteDefinition("kubernetes_check", "/v1/kubernetes/check", ("POST",)),
    AgentdRouteDefinition("kubernetes_run", "/v1/kubernetes/run", ("POST",)),
    AgentdRouteDefinition("kubernetes_evidence", "/v1/kubernetes/evidence", ("POST",)),
)
_KUBERNETES_ROUTES = AgentdRouteMatcher(_KUBERNETES_ROUTE_DEFINITIONS)

_COMMAND_NAMES = {
    "kubernetes_preflight": "preflight",
    "kubernetes_model_probe": "model-probe",
    "kubernetes_check": "check",
    "kubernetes_run": "run",
    "kubernetes_evidence": "evidence",
}


def _text(body: JsonMapping, key: str) -> str | None:
    value = body.get(key)
    return value if isinstance(value, str) and value else None


def _flag(body: JsonMapping, key: str) -> bool:
    value = body.get(key)
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.lower() == "true"


async def handle_kubernetes_route(
    service: RuntimeService,
    request: HttpRequest,
    method: str,
    path: str,
) -> HttpResponse | None:
    route = _KUBERNETES_ROUTES.match(path, method)
    if route is None:
        return None
    if not route.method_allowed:
        return method_not_allowed(route.allowed_methods)

    body = request.body
    workload = _text(body, "workload")
    if workload is None:
        return bad_request("workload is required")

    args = argparse.Namespace(
        kubernetes_command=_COMMAND_NAMES[route.name],
        profile=_text(body, "profile") or "local-kubernetes",
        profile_config=_text(body, "profile_config"),
        workload=workload,
        namespace=_text(body, "namespace"),
        skip_preflight=_flag(body, "skip_preflight"),
        skip_model_probe=_flag(body, "skip_model_probe"),
        skip_cluster=_flag(body, "skip_cluster"),
    )
    try:
        result = await dispatch_kubernetes(args, service)
    except ValueError as exc:
        return bad_request(str(exc))
    return json_response(immutable_json(dict(result.payload)))


def match_kubernetes_route(path: str, method: str) -> AgentdRouteMatch | None:
    """Expose the matcher so the app can short-circuit routing."""

    return _KUBERNETES_ROUTES.match(path, method)
