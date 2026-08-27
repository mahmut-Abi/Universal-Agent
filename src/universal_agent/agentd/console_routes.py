from __future__ import annotations

from collections.abc import Callable

from universal_agent.agentd.http import (
    HttpRequest,
    HttpResponse,
    bad_request,
    method_not_allowed,
    not_found,
    text_response,
)
from universal_agent.agentd.routing import (
    AgentdRouteDefinition,
    AgentdRouteMatcher,
    _console_domain_package_view,
    _console_domain_view,
    _domain_not_found_message,
    _domain_package_not_found_message,
    _optional_positive_int_query,
    _optional_query_value,
    _optional_session_id_query,
)
from universal_agent.core import SessionId
from universal_agent.evaluation.console import (
    EvaluationConsoleSnapshot,
    build_evaluation_console_snapshot,
    render_evaluation_console,
)
from universal_agent.service import RuntimeService
from universal_agent.state import StateNotFoundError
from universal_agent.web import (
    build_web_console_snapshot,
    render_web_catalog,
    render_web_console,
    render_web_distributed,
    render_web_doctor,
    render_web_domain_detail,
    render_web_domain_package_detail,
    render_web_evidence_explorer,
    render_web_multi_agent,
    render_web_profile_catalog,
    render_web_session_detail,
    render_web_sessions,
    render_web_settings,
    render_web_world_model_explorer,
)
from universal_agent.web_types import WebCatalogPage, WebConsoleSnapshot

_CONSOLE_ROUTES = AgentdRouteMatcher(
    (
        AgentdRouteDefinition("console_root", "/"),
        AgentdRouteDefinition("console_root", "/console"),
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
        AgentdRouteDefinition("console_session_suffix", "/console/sessions/{session_id}/{suffix}"),
        AgentdRouteDefinition("console_session", "/console/sessions/{session_id}"),
        AgentdRouteDefinition("console_catalog", "/console/{page}"),
    )
)

_STATIC_CONSOLE_RENDERERS: dict[str, Callable[[WebConsoleSnapshot], str]] = {
    "console_settings": render_web_settings,
    "console_profiles": render_web_profile_catalog,
    "console_doctor": lambda snapshot: render_web_doctor(snapshot, snapshot.doctor),
    "console_distributed": lambda snapshot: render_web_distributed(
        snapshot,
        snapshot.distributed_snapshot,
        snapshot.distributed_health,
    ),
    "console_multi_agent": render_web_multi_agent,
    "console_sessions": render_web_sessions,
    "console_root": render_web_console,
}

_SESSION_CONSOLE_RENDERERS: dict[str, Callable[[WebConsoleSnapshot], str]] = {
    "": render_web_session_detail,
    "evidence": render_web_evidence_explorer,
    "world": render_web_world_model_explorer,
}


async def handle_console_route(
    service: RuntimeService,
    evaluation_report_dir: str | None,
    request: HttpRequest,
    method: str,
    path: str,
) -> HttpResponse | None:
    route = _CONSOLE_ROUTES.match(path, method)
    if route is None:
        return None

    catalog = None
    if route.name == "console_catalog":
        try:
            catalog = WebCatalogPage(route.path_params["page"])
        except ValueError:
            return None

    if not route.method_allowed:
        return method_not_allowed(route.allowed_methods)

    if route.name == "console_evaluations":
        evaluation_snapshot = (
            EvaluationConsoleSnapshot("not configured", ())
            if evaluation_report_dir is None
            else build_evaluation_console_snapshot(evaluation_report_dir)
        )
        return text_response(
            render_evaluation_console(evaluation_snapshot),
            content_type="text/html; charset=utf-8",
        )

    if renderer := _STATIC_CONSOLE_RENDERERS.get(route.name):
        return await _render_console_snapshot(
            service,
            request,
            renderer,
            session_id=_optional_session_id_query(request.path)
            if route.name == "console_root"
            else None,
        )

    if route.name in {"console_evidence", "console_world"}:
        is_world_explorer = route.name == "console_world"
        return await _render_console_snapshot(
            service,
            request,
            render_web_world_model_explorer if is_world_explorer else render_web_evidence_explorer,
            session_id=_optional_session_id_query(request.path),
            world_entity_id=(
                _optional_query_value(request.path, "entity_id") if is_world_explorer else None
            ),
            world_relation=(
                _optional_query_value(request.path, "relation") if is_world_explorer else None
            ),
        )

    if route.name == "console_catalog":
        assert catalog is not None
        return await _render_console_snapshot(
            service,
            request,
            lambda snapshot: render_web_catalog(snapshot, catalog),
        )

    if route.name in {"console_domain_package", "console_domain_package_version"}:
        package_name = route.path_params["name"]
        package_version = route.path_params.get("version")
        try:
            snapshot = await _build_console_snapshot(service, request)
        except ValueError as exc:
            return bad_request(str(exc))
        package = _console_domain_package_view(
            snapshot,
            package_name,
            package_version,
        )
        if package is None:
            return not_found(
                _domain_package_not_found_message(
                    package_name,
                    package_version,
                )
            )
        return text_response(
            render_web_domain_package_detail(
                snapshot,
                package_name=package.name,
                package_version=package.version,
            ),
            content_type="text/html; charset=utf-8",
        )

    if route.name in {"console_domain", "console_domain_version"}:
        domain_name = route.path_params["name"]
        domain_version = route.path_params.get("version")
        try:
            snapshot = await _build_console_snapshot(service, request)
        except ValueError as exc:
            return bad_request(str(exc))
        domain = _console_domain_view(snapshot, domain_name, domain_version)
        if domain is None:
            return not_found(_domain_not_found_message(domain_name, domain_version))
        return text_response(
            render_web_domain_detail(
                snapshot,
                domain_name=domain.name,
                domain_version=domain.version,
            ),
            content_type="text/html; charset=utf-8",
        )

    if route.name in {"console_session", "console_session_suffix"}:
        session_id = SessionId(route.path_params["session_id"])
        session_suffix = route.path_params.get("suffix", "")
        is_world_explorer = session_suffix == "world"
        try:
            snapshot = await _build_console_snapshot(
                service,
                request,
                session_id=session_id,
                world_entity_id=(
                    _optional_query_value(request.path, "entity_id") if is_world_explorer else None
                ),
                world_relation=(
                    _optional_query_value(request.path, "relation") if is_world_explorer else None
                ),
            )
        except StateNotFoundError as exc:
            return not_found(str(exc))
        except ValueError as exc:
            return bad_request(str(exc))
        renderer = _SESSION_CONSOLE_RENDERERS.get(session_suffix)
        if renderer is None:
            return not_found(f"unknown route: {path}")
        return text_response(
            renderer(snapshot),
            content_type="text/html; charset=utf-8",
        )
    return None


async def _render_console_snapshot(
    service: RuntimeService,
    request: HttpRequest,
    renderer: Callable[[WebConsoleSnapshot], str],
    *,
    session_id: SessionId | None = None,
    world_entity_id: str | None = None,
    world_relation: str | None = None,
) -> HttpResponse:
    try:
        snapshot = await _build_console_snapshot(
            service,
            request,
            session_id=session_id,
            world_entity_id=world_entity_id,
            world_relation=world_relation,
        )
    except StateNotFoundError as exc:
        return not_found(str(exc))
    except ValueError as exc:
        return bad_request(str(exc))
    return text_response(renderer(snapshot), content_type="text/html; charset=utf-8")


async def _build_console_snapshot(
    service: RuntimeService,
    request: HttpRequest,
    *,
    session_id: SessionId | None = None,
    world_entity_id: str | None = None,
    world_relation: str | None = None,
) -> WebConsoleSnapshot:
    return await build_web_console_snapshot(
        service,
        session_id=session_id,
        session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
        event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
        world_entity_id=world_entity_id,
        world_relation=world_relation,
    )
