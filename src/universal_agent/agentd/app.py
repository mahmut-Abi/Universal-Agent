from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from universal_agent.agentd._routes_distributed import (
    _DISTRIBUTED_ROUTE_DEFINITIONS,
    DistributedRouteHandlers,
)
from universal_agent.agentd._routes_eval import (
    ecosystem_route_definitions,
    eval_route_definitions,
    handle_ecosystem_route,
    handle_eval_route,
)
from universal_agent.agentd._routes_session import (
    _SESSION_ROUTE_DEFINITIONS,
    SessionRouteHandlers,
)
from universal_agent.agentd.config_admin_routes import (
    config_admin_route_definitions,
    handle_config_admin_route,
)
from universal_agent.agentd.console_routes import handle_console_route
from universal_agent.agentd.contributions import load_route_contributions
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
    _memory_create_payload,
    _normalize_path,
    _optional_query_value,
)
from universal_agent.core import GoalStatus, JsonMapping, immutable_json
from universal_agent.domain import AmbiguousDomainPackageError, DomainPackageNotFoundError
from universal_agent.host_contracts import DomainRouteContribution
from universal_agent.memory import MemoryKind
from universal_agent.profile import ProfileConfigNotFoundError, ProfileNotFoundError
from universal_agent.profile.store import ProfileStore
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

_MEMORY_ROUTE_DEFINITIONS = (
    AgentdRouteDefinition("memory_create", "/v1/memory", ("POST",)),
    AgentdRouteDefinition(
        "memory_record",
        "/v1/memory/{memory_id}",
        ("GET", "DELETE"),
    ),
)
_MEMORY_ROUTES = AgentdRouteMatcher(_MEMORY_ROUTE_DEFINITIONS)

_OPENAPI_ROUTE_DEFINITIONS = (
    *_STATIC_GET_ROUTE_DEFINITIONS,
    *_DETAIL_GET_ROUTE_DEFINITIONS,
    *config_admin_route_definitions(),
    *eval_route_definitions(),
    *ecosystem_route_definitions(),
    *_MEMORY_ROUTE_DEFINITIONS,
    *_DISTRIBUTED_ROUTE_DEFINITIONS,
    *_SESSION_ROUTE_DEFINITIONS,
)


def _all_route_definitions() -> tuple[AgentdRouteDefinition, ...]:
    """Base routes plus domain-contributed routes (entry-point discovered)."""

    return (
        *_OPENAPI_ROUTE_DEFINITIONS,
        *(
            AgentdRouteDefinition(d.name, d.template, d.methods)
            for c in load_route_contributions()
            for d in c.route_definitions
        ),
    )


_PROFILE_HEADER = "x-profile"
_ACTIVE_GOAL_STATUSES = frozenset({GoalStatus.RUNNING, GoalStatus.WAITING})


@dataclass(slots=True)
class _ServiceBundle:
    """A RuntimeService plus its bound route handlers.

    The startup service is the default bundle; hot-swapped profiles get their
    own bundle built lazily through ``profile_service_factory``.
    """

    service: RuntimeService
    session: SessionRouteHandlers
    distributed: DistributedRouteHandlers

    @classmethod
    def build(cls, service: RuntimeService) -> _ServiceBundle:
        return cls(
            service,
            SessionRouteHandlers(service),
            DistributedRouteHandlers(service),
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
        profile_store: ProfileStore | None = None,
        profile_service_factory: Callable[[str], RuntimeService] | None = None,
    ) -> None:
        self._default_bundle = _ServiceBundle.build(service)
        self._default_profile_names = frozenset(item.name for item in service.profiles())
        self._profile_service_factory = profile_service_factory
        self._profile_bundles: dict[str, _ServiceBundle] = {}
        self._bundle_lock = threading.Lock()
        self._route_contributions: tuple[DomainRouteContribution, ...] = load_route_contributions()
        self._auth = auth or AgentdAuthPolicy()
        self._evaluation_report_dir = (
            None if evaluation_report_dir is None else str(evaluation_report_dir)
        )
        self._profile_store = profile_store

    @property
    def service(self) -> RuntimeService:
        """The default (startup) service, for hosts/tests that need it directly."""

        return self._default_bundle.service

    def _bundle_for(self, request: HttpRequest) -> tuple[_ServiceBundle, HttpResponse | None]:
        """Resolve the service bundle for a request.

        Requests without an ``X-Profile`` header — or naming a profile the
        startup service already hosts — use the default bundle. Other profile
        names are built lazily through ``profile_service_factory`` and cached.
        """

        profile = request.headers.get(_PROFILE_HEADER)
        if not profile:
            return self._default_bundle, None
        if profile in self._default_profile_names:
            return self._default_bundle, None
        if self._profile_service_factory is None:
            return (
                self._default_bundle,
                bad_request("profile hot-swap is not configured on this server"),
            )
        with self._bundle_lock:
            bundle = self._profile_bundles.get(profile)
            if bundle is None:
                try:
                    bundle = _ServiceBundle.build(self._profile_service_factory(profile))
                except ProfileConfigNotFoundError:
                    return self._default_bundle, not_found(f"profile config not found: {profile}")
                except Exception as exc:
                    return self._default_bundle, bad_request(
                        f"failed to build service for profile {profile!r}: {exc}"
                    )
                self._profile_bundles[profile] = bundle
            return bundle, None

    async def _has_active_sessions(self, bundle: _ServiceBundle) -> bool:
        """Whether the bundle's service has running or waiting sessions."""

        summaries = await bundle.service.list_sessions()
        return any(item.goal_status in _ACTIVE_GOAL_STATUSES for item in summaries)

    async def _profile_mutation_guard(
        self,
        request: HttpRequest,
        method: str,
        path: str,
    ) -> HttpResponse | None:
        """Refuse profile mutations that would strand an active service.

        A hot-swapped bundle holds runtime state (sessions). Mutating its
        profile while a session is RUNNING/WAITING would silently orphan the
        execution (AGENTS.md §4.9: agents are autonomous execution
        boundaries), so the mutation is rejected with 409 until the session
        settles. PATCH/DELETE on a settled profile invalidate the cached
        bundle so the next request rebuilds from the new config.
        """

        if method not in {"PATCH", "DELETE"}:
            return None
        prefix = "/v1/profiles/"
        if not path.startswith(prefix):
            return None
        profile = path[len(prefix) :].split("/", 1)[0]
        if not profile:
            return None
        bundle = self._profile_bundles.get(profile)
        if bundle is None:
            return None
        if await self._has_active_sessions(bundle):
            return json_response(
                immutable_json(
                    {
                        "error": {
                            "code": "profile_in_use",
                            "message": (
                                f"profile {profile!r} has running or waiting sessions; "
                                "settle them before changing the profile"
                            ),
                        }
                    }
                ),
                status_code=409,
            )
        # Settled: drop the cached bundle so the next request picks up the
        # updated profile config.
        with self._bundle_lock:
            self._profile_bundles.pop(profile, None)
        return None

    async def handle(self, request: HttpRequest) -> HttpResponse:
        method = request.method.upper()
        path = _normalize_path(request.path)

        auth_response = _authenticate(self._auth, request, path, method=method)
        if auth_response is not None:
            return auth_response

        bundle, bundle_error = self._bundle_for(request)
        if bundle_error is not None:
            return bundle_error
        service = bundle.service

        memory_response = self._memory_route_response(request, method, path, service)
        if memory_response is not None:
            return memory_response

        # Config-management write plane runs before the static GET routes so
        # POST/PATCH/DELETE on /v1/profiles are not swallowed by the runtime
        # read models (GET stays authoritative for loaded profiles). Mutations
        # on hot-swapped profiles are guarded first: an active session blocks
        # the change with 409 instead of being stranded by the rebuild.
        mutation_guard = await self._profile_mutation_guard(request, method, path)
        if mutation_guard is not None:
            return mutation_guard
        config_admin_response = await handle_config_admin_route(
            self._profile_store, request, method, path
        )
        if config_admin_response is not None:
            return config_admin_response

        static_response = await self._static_get_route_response(request, method, path, service)
        if static_response is not None:
            return static_response

        for contribution in self._route_contributions:
            domain_response = await contribution.handle(service, method, path, request.body)
            if domain_response is not None:
                if domain_response.headers:
                    return HttpResponse(
                        status_code=domain_response.status_code,
                        body=domain_response.body,
                        headers=domain_response.headers,
                    )
                return json_response(
                    domain_response.body,
                    status_code=domain_response.status_code,
                )
        eval_response = await handle_eval_route(service, request, method, path)
        if eval_response is not None:
            return eval_response
        ecosystem_response = handle_ecosystem_route(service, request, method, path)
        if ecosystem_response is not None:
            return ecosystem_response
        console_response = await handle_console_route(
            service,
            self._evaluation_report_dir,
            request,
            method,
            path,
        )
        if console_response is not None:
            return console_response
        detail_response = await self._detail_get_route_response(method, path, service)
        if detail_response is not None:
            return detail_response

        distributed_response = await bundle.distributed.route_response(request, method, path)
        if distributed_response is not None:
            return distributed_response

        session_response = await bundle.session.route_response(request, method, path)
        if session_response is not None:
            return session_response

        return not_found(f"unknown route: {path}")

    @staticmethod
    def _domain_filter_matches(request: HttpRequest, domain_name: str) -> bool:
        """Check the ``domain`` query param against a catalog item's domain."""

        query = _optional_query_value(request.path, "domain")
        return query is None or query == domain_name

    def _memory_route_response(
        self,
        request: HttpRequest,
        method: str,
        path: str,
        service: RuntimeService,
    ) -> HttpResponse | None:
        route = _MEMORY_ROUTES.match(path, method)
        if route is None or not route.method_allowed:
            return None  # fall through (GET /v1/memory list is served by the static routes)
        memory_id = route.path_params.get("memory_id")
        if route.name == "memory_create":
            try:
                payload = _memory_create_payload(request.body)
                view = service.create_memory(
                    kind=MemoryKind(payload.kind),
                    subject=payload.subject,
                    content=payload.content,
                    scope=payload.scope,
                    confidence=payload.confidence,
                )
            except ValueError as exc:
                return bad_request(str(exc))
            return json_response(memory_body(view), status_code=201)
        assert memory_id is not None
        existing = service.get_memory(memory_id)
        if existing is None:
            return not_found(f"memory record not found: {memory_id}")
        if method == "DELETE":
            service.delete_memory(memory_id)
            return json_response({"deleted": True, "memory_id": memory_id})
        return json_response(memory_body(existing))

    async def _static_get_route_response(
        self,
        request: HttpRequest,
        method: str,
        path: str,
        service: RuntimeService,
    ) -> HttpResponse | None:
        route = _STATIC_GET_ROUTES.match(path, method)
        if route is None:
            return None
        if not route.method_allowed:
            return method_not_allowed(route.allowed_methods)
        if route.name == "openapi":
            return json_response(build_agentd_openapi_schema(_all_route_definitions()))

        sync_json_handlers: dict[str, Callable[[], JsonMapping]] = {
            "health": lambda: health_body(service.health()),
            "ready": lambda: ready_body(service.ready()),
            "domains": lambda: immutable_json(
                {"domains": [domain_body(item) for item in service.domains()]}
            ),
            "capabilities": lambda: immutable_json(
                {"capabilities": [capability_body(item) for item in service.capabilities()]}
            ),
            "tools": lambda: immutable_json(
                {
                    "tools": [
                        tool_body(item)
                        for item in service.tools()
                        if self._domain_filter_matches(request, item.domain_name)
                    ]
                }
            ),
            "policies": lambda: immutable_json(
                {"policies": [policy_body(item) for item in service.policies()]}
            ),
            "evaluators": lambda: immutable_json(
                {"evaluators": [evaluator_body(item) for item in service.evaluators()]}
            ),
            "memory": lambda: immutable_json(
                {"memories": [memory_body(item) for item in service.memories()]}
            ),
            "profiles": lambda: immutable_json(
                {"profiles": [profile_body(item) for item in service.profiles()]}
            ),
            "multi_agent": lambda: multi_agent_body(service.multi_agent()),
            "config": lambda: config_body(service.config()),
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
                            domain_package_body(item) for item in service.domain_packages(tag=tag)
                        ]
                    }
                )
            )
        if route.name == "distributed_snapshot":
            distributed_snapshot = service.distributed_snapshot()
            if distributed_snapshot is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_snapshot_body(distributed_snapshot))
        if route.name == "distributed_health":
            health = service.distributed_health()
            if health is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_health_body(health))
        if route.name == "prometheus_scrape":
            return text_response(
                await service.prometheus_metrics(),
                content_type="text/plain; version=0.0.4; charset=utf-8",
            )
        if route.name == "metrics":
            return json_response(metrics_body(await service.metrics()))
        if route.name == "metrics_prometheus":
            return text_response(
                await service.prometheus_metrics(),
                content_type="text/plain; version=0.0.4; charset=utf-8",
            )
        if route.name == "cost":
            return json_response(cost_body(await service.cost()))
        if route.name == "logs":
            return json_response(log_records_body(await service.logs()))
        if route.name == "traces":
            return json_response(trace_spans_body(await service.traces()))
        if route.name == "traces_otlp":
            return json_response(await service.opentelemetry_traces())
        if route.name == "doctor":
            return json_response(doctor_body(await service.doctor()))
        if route.name == "audit":
            return json_response(audit_records_body(await service.audit_records()))
        if route.name == "audit_integrity":
            return json_response(audit_integrity_body(await service.audit_integrity()))
        return None

    async def _detail_get_route_response(
        self, method: str, path: str, service: RuntimeService
    ) -> HttpResponse | None:
        route = _DETAIL_GET_ROUTES.match(path, method)
        if route is None:
            return None
        if not route.method_allowed:
            return method_not_allowed(route.allowed_methods)

        if route.name == "profile":
            try:
                return json_response(profile_body(service.profile(route.path_params["profile"])))
            except ProfileNotFoundError as exc:
                return not_found(str(exc))
        if route.name in {"domain_package", "domain_package_version"}:
            try:
                return json_response(
                    domain_package_body(
                        service.domain_package(
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
