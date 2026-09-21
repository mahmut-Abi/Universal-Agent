"""Kubernetes Domain contribution to the agentd HTTP surface.

Exposes the kubernetes operator flow (preflight, model probe, check, run,
evidence) over the Runtime API so clients no longer need the CLI dispatch
for remote operation. The payloads mirror the CLI output bodies exactly.

The agentd host discovers this module through the
``universal_agent.agentd_routes`` entry-point group; it never imports a
concrete domain, and this module never imports the agentd adapter.
"""

from __future__ import annotations

import argparse

from universal_agent.core import JsonMapping, immutable_json
from universal_agent.domains.kubernetes.cli_reports import dispatch_kubernetes
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

_KUBERNETES_ROUTE_DEFINITIONS = (
    DomainRouteDefinition("kubernetes_preflight", "/v1/kubernetes/preflight", ("POST",)),
    DomainRouteDefinition("kubernetes_model_probe", "/v1/kubernetes/model-probe", ("POST",)),
    DomainRouteDefinition("kubernetes_check", "/v1/kubernetes/check", ("POST",)),
    DomainRouteDefinition("kubernetes_run", "/v1/kubernetes/run", ("POST",)),
    DomainRouteDefinition("kubernetes_evidence", "/v1/kubernetes/evidence", ("POST",)),
)

_COMMAND_NAMES = {
    "kubernetes_preflight": "preflight",
    "kubernetes_model_probe": "model-probe",
    "kubernetes_check": "check",
    "kubernetes_run": "run",
    "kubernetes_evidence": "evidence",
}

_KUBERNETES_OPENAPI_METADATA: dict[str, tuple[str, str, str]] = {
    "kubernetes_preflight": (
        "Kubernetes preflight",
        "Run the kubernetes preflight checks for a workload.",
        "Kubernetes",
    ),
    "kubernetes_model_probe": (
        "Kubernetes model probe",
        "Probe the configured model's decision contract for a workload.",
        "Kubernetes",
    ),
    "kubernetes_check": (
        "Kubernetes check",
        "Run model probe followed by preflight for a workload.",
        "Kubernetes",
    ),
    "kubernetes_run": (
        "Kubernetes remediation run",
        "Run the full kubernetes remediation operator flow for a workload.",
        "Kubernetes",
    ),
    "kubernetes_evidence": (
        "Kubernetes evidence",
        "Run remediation and return the collected evidence for the session.",
        "Kubernetes",
    ),
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
    method: str,
    path: str,
    body: JsonMapping,
) -> DomainRouteResponse | None:
    match = match_domain_route(_KUBERNETES_ROUTE_DEFINITIONS, method, path)
    if match is None:
        return None
    route, method_allowed = match
    if not method_allowed:
        return domain_method_not_allowed(route.methods)

    skip_cluster = _flag(body, "skip_cluster")
    workload = _text(body, "workload")
    # preflight treats the workload as optional (it adds a workload inspection
    # check when present); every other operator route requires a target
    # (UA-LIVE-2026-09-21 P6).
    if workload is None and route.name != "kubernetes_preflight":
        return domain_bad_request("workload is required")

    args = argparse.Namespace(
        kubernetes_command=_COMMAND_NAMES[route.name],
        profile=_text(body, "profile") or "local-kubernetes",
        profile_config=_text(body, "profile_config"),
        workload=workload,
        namespace=_text(body, "namespace"),
        skip_preflight=_flag(body, "skip_preflight"),
        skip_model_probe=_flag(body, "skip_model_probe"),
        skip_cluster=skip_cluster,
        submit_run=_flag(body, "submit_run"),
        dry_run=_flag(body, "read_only"),
    )
    try:
        result = await dispatch_kubernetes(args, service)
    except ValueError as exc:
        return domain_bad_request(str(exc))
    return domain_json_response(immutable_json(dict(result.payload)))


def kubernetes_agentd_contribution() -> DomainRouteContribution:
    """Entry-point factory for the kubernetes agentd route contribution."""

    return DomainRouteContribution(
        domain="kubernetes",
        route_definitions=_KUBERNETES_ROUTE_DEFINITIONS,
        handle=handle_kubernetes_route,
        openapi_metadata=_KUBERNETES_OPENAPI_METADATA,
        openapi_tags=(("Kubernetes", "Kubernetes incident-response operator surface"),),
    )
