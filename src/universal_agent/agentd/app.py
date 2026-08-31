from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from universal_agent.agentd._routes_distributed import (
    _DISTRIBUTED_ROUTE_DEFINITIONS,
    DistributedRouteHandlers,
)
from universal_agent.agentd._routes_session import (
    _SESSION_ROUTE_DEFINITIONS,
    SessionRouteHandlers,
)
from universal_agent.agentd.console_routes import handle_console_route
from universal_agent.agentd.http import (
    AgentdAuthPolicy,
    HttpRequest,
    HttpResponse,
    _authenticate,
    bad_request,
    json_response,
    method_not_allowed,
    not_found,
    text_response,
)
from universal_agent.agentd.openapi import build_agentd_openapi_schema
from universal_agent.agentd.representations import (
    audit_integrity_body,
    audit_records_body,
    capability_body,
    config_body,
    cost_body,
    distributed_health_body,
    distributed_snapshot_body,
    doctor_body,
    domain_body,
    domain_package_body,
    evaluator_body,
    health_body,
    log_records_body,
    memory_body,
    metrics_body,
    multi_agent_body,
    policy_body,
    profile_body,
    ready_body,
    tool_body,
    trace_spans_body,
)
from universal_agent.agentd.routing import (
    AgentdRouteDefinition,
    AgentdRouteMatcher,
    _normalize_path,
    _optional_query_value,
)
from universal_agent.core import JsonMapping, immutable_json
from universal_agent.domain import AmbiguousDomainPackageError, DomainPackageNotFoundError
from universal_agent.profile import ProfileNotFoundError
from universal_agent.service import RuntimeService

_STATIC_GET_ROUTE_DEFINITIONS = (
    AgentdRouteDefinition("openapi", "/openapi.json"),
    AgentdRouteDefinition("health", "/health"),
    AgentdRouteDefinition("ready", "/ready"),
    AgentdRouteDefinition("prometheus_scrape", "/metrics"),
    AgentdRouteDefinition("domains", "/v1/domains"),
    AgentdRouteDefinition("domain_packages", "/v1/domain-packages"),
    AgentdRouteDefinition("capabilities", "/v1/capabilities"),
    AgentdRouteDefinition("tools", "/v1/tools"),
    AgentdRouteDefinition("policies", "/v1/policies"),
    AgentdRouteDefinition("evaluators", "/v1/evaluators"),
    AgentdRouteDefinition("memory", "/v1/memory"),
    AgentdRouteDefinition("profiles", "/v1/profiles"),
    AgentdRouteDefinition("multi_agent", "/v1/multi-agent"),
    AgentdRouteDefinition("config", "/v1/config"),
    AgentdRouteDefinition("distributed_snapshot", "/v1/distributed/snapshot"),
    AgentdRouteDefinition("distributed_health", "/v1/distributed/health"),
    AgentdRouteDefinition("metrics", "/v1/metrics"),
    AgentdRouteDefinition("metrics_prometheus", "/v1/metrics/prometheus"),
    AgentdRouteDefinition("cost", "/v1/cost"),
    AgentdRouteDefinition("logs", "/v1/logs"),
    AgentdRouteDefinition("traces", "/v1/traces"),
    AgentdRouteDefinition("traces_otlp", "/v1/traces/otlp"),
    AgentdRouteDefinition("doctor", "/v1/doctor"),
    AgentdRouteDefinition("audit", "/v1/audit"),
    AgentdRouteDefinition("audit_integrity", "/v1/audit/integrity"),
)
_STATIC_GET_ROUTES = AgentdRouteMatcher(_STATIC_GET_ROUTE_DEFINITIONS)


_DETAIL_GET_ROUTE_DEFINITIONS = (
    AgentdRouteDefinition("profile", "/v1/profiles/{profile}"),
    AgentdRouteDefinition("domain_package", "/v1/domain-packages/{name}"),
    AgentdRouteDefinition("domain_package_version", "/v1/domain-packages/{name}/{version}"),
)
_DETAIL_GET_ROUTES = AgentdRouteMatcher(_DETAIL_GET_ROUTE_DEFINITIONS)

_OPENAPI_ROUTE_DEFINITIONS = (
    *_STATIC_GET_ROUTE_DEFINITIONS,
    *_DETAIL_GET_ROUTE_DEFINITIONS,
    *_DISTRIBUTED_ROUTE_DEFINITIONS,
    *_SESSION_ROUTE_DEFINITIONS,
)


class AgentdApp:
    """Runtime API route adapter for the agentd process.

    It owns HTTP-shaped routing and JSON serialization. Runtime behavior stays
    behind RuntimeService, so the ASGI server boundary can stay independent of
    Kernel internals.
    """

    def __init__(
        self,
        service: RuntimeService,
        auth: AgentdAuthPolicy | None = None,
        *,
        evaluation_report_dir: str | Path | None = None,
    ) -> None:
        self._service = service
        self._distributed = DistributedRouteHandlers(service)
        self._session = SessionRouteHandlers(service)
        self._auth = auth or AgentdAuthPolicy()
        self._evaluation_report_dir = (
            None if evaluation_report_dir is None else str(evaluation_report_dir)
        )

    async def handle(self, request: HttpRequest) -> HttpResponse:
        method = request.method.upper()
        path = _normalize_path(request.path)

        auth_response = _authenticate(self._auth, request, path, method=method)
        if auth_response is not None:
            return auth_response

        static_response = await self._static_get_route_response(request, method, path)
        if static_response is not None:
            return static_response

        console_response = await handle_console_route(
            self._service,
            self._evaluation_report_dir,
            request,
            method,
            path,
        )
        if console_response is not None:
            return console_response
        detail_response = await self._detail_get_route_response(method, path)
        if detail_response is not None:
            return detail_response

        distributed_response = await self._distributed.route_response(request, method, path)
        if distributed_response is not None:
            return distributed_response

        session_response = await self._session.route_response(request, method, path)
        if session_response is not None:
            return session_response

        return not_found(f"unknown route: {path}")

    async def _static_get_route_response(
        self,
        request: HttpRequest,
        method: str,
        path: str,
    ) -> HttpResponse | None:
        route = _STATIC_GET_ROUTES.match(path, method)
        if route is None:
            return None
        if not route.method_allowed:
            return method_not_allowed(route.allowed_methods)
        if route.name == "openapi":
            return json_response(build_agentd_openapi_schema(_OPENAPI_ROUTE_DEFINITIONS))

        sync_json_handlers: dict[str, Callable[[], JsonMapping]] = {
            "health": lambda: health_body(self._service.health()),
            "ready": lambda: ready_body(self._service.ready()),
            "domains": lambda: immutable_json(
                {"domains": [domain_body(item) for item in self._service.domains()]}
            ),
            "capabilities": lambda: immutable_json(
                {"capabilities": [capability_body(item) for item in self._service.capabilities()]}
            ),
            "tools": lambda: immutable_json(
                {"tools": [tool_body(item) for item in self._service.tools()]}
            ),
            "policies": lambda: immutable_json(
                {"policies": [policy_body(item) for item in self._service.policies()]}
            ),
            "evaluators": lambda: immutable_json(
                {"evaluators": [evaluator_body(item) for item in self._service.evaluators()]}
            ),
            "memory": lambda: immutable_json(
                {"memories": [memory_body(item) for item in self._service.memories()]}
            ),
            "profiles": lambda: immutable_json(
                {"profiles": [profile_body(item) for item in self._service.profiles()]}
            ),
            "multi_agent": lambda: multi_agent_body(self._service.multi_agent()),
            "config": lambda: config_body(self._service.config()),
        }
        if handler := sync_json_handlers.get(route.name):
            return json_response(handler())

        if route.name == "domain_packages":
            try:
                tag = _optional_query_value(request.path, "tag")
            except ValueError as exc:
                return bad_request(str(exc))
            return json_response(
                immutable_json(
                    {
                        "domain_packages": [
                            domain_package_body(item)
                            for item in self._service.domain_packages(tag=tag)
                        ]
                    }
                )
            )
        if route.name == "distributed_snapshot":
            distributed_snapshot = self._service.distributed_snapshot()
            if distributed_snapshot is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_snapshot_body(distributed_snapshot))
        if route.name == "distributed_health":
            health = self._service.distributed_health()
            if health is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_health_body(health))
        if route.name == "prometheus_scrape":
            return text_response(
                await self._service.prometheus_metrics(),
                content_type="text/plain; version=0.0.4; charset=utf-8",
            )
        if route.name == "metrics":
            return json_response(metrics_body(await self._service.metrics()))
        if route.name == "metrics_prometheus":
            return text_response(
                await self._service.prometheus_metrics(),
                content_type="text/plain; version=0.0.4; charset=utf-8",
            )
        if route.name == "cost":
            return json_response(cost_body(await self._service.cost()))
        if route.name == "logs":
            return json_response(log_records_body(await self._service.logs()))
        if route.name == "traces":
            return json_response(trace_spans_body(await self._service.traces()))
        if route.name == "traces_otlp":
            return json_response(await self._service.opentelemetry_traces())
        if route.name == "doctor":
            return json_response(doctor_body(await self._service.doctor()))
        if route.name == "audit":
            return json_response(audit_records_body(await self._service.audit_records()))
        if route.name == "audit_integrity":
            return json_response(audit_integrity_body(await self._service.audit_integrity()))
        return None

    async def _detail_get_route_response(self, method: str, path: str) -> HttpResponse | None:
        route = _DETAIL_GET_ROUTES.match(path, method)
        if route is None:
            return None
        if not route.method_allowed:
            return method_not_allowed(route.allowed_methods)

        if route.name == "profile":
            try:
                return json_response(
                    profile_body(self._service.profile(route.path_params["profile"]))
                )
            except ProfileNotFoundError as exc:
                return not_found(str(exc))
        if route.name in {"domain_package", "domain_package_version"}:
            try:
                return json_response(
                    domain_package_body(
                        self._service.domain_package(
                            route.path_params["name"],
                            route.path_params.get("version"),
                        )
                    )
                )
            except DomainPackageNotFoundError as exc:
                return not_found(str(exc))
            except AmbiguousDomainPackageError as exc:
                return bad_request(str(exc))
        return None
