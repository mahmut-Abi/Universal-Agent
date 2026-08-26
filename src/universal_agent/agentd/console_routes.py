from __future__ import annotations

from universal_agent.agentd.http import (
    HttpRequest,
    HttpResponse,
    bad_request,
    method_not_allowed,
    not_found,
    text_response,
)
from universal_agent.agentd.routing import (
    _console_catalog_route,
    _console_domain_package_route,
    _console_domain_package_view,
    _console_domain_route,
    _console_domain_view,
    _console_explorer_renderer,
    _console_session_renderer,
    _console_session_route,
    _domain_not_found_message,
    _domain_package_not_found_message,
    _optional_positive_int_query,
    _optional_query_value,
    _optional_session_id_query,
)
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
    render_web_multi_agent,
    render_web_profile_catalog,
    render_web_sessions,
    render_web_settings,
)


async def handle_console_route(
    service: RuntimeService,
    evaluation_report_dir: str | None,
    request: HttpRequest,
    method: str,
    path: str,
) -> HttpResponse | None:
    if path == "/console/settings":
        if method != "GET":
            return method_not_allowed(("GET",))
        try:
            snapshot = await build_web_console_snapshot(
                service,
                session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
            )
        except ValueError as exc:
            return bad_request(str(exc))
        return text_response(
            render_web_settings(snapshot),
            content_type="text/html; charset=utf-8",
        )
    if path == "/console/profiles":
        if method != "GET":
            return method_not_allowed(("GET",))
        try:
            snapshot = await build_web_console_snapshot(
                service,
                session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
            )
        except ValueError as exc:
            return bad_request(str(exc))
        return text_response(
            render_web_profile_catalog(snapshot),
            content_type="text/html; charset=utf-8",
        )
    if path == "/console/evaluations":
        if method != "GET":
            return method_not_allowed(("GET",))
        evaluation_snapshot = (
            EvaluationConsoleSnapshot("not configured", ())
            if evaluation_report_dir is None
            else build_evaluation_console_snapshot(evaluation_report_dir)
        )
        return text_response(
            render_evaluation_console(evaluation_snapshot),
            content_type="text/html; charset=utf-8",
        )
    if path == "/console/doctor":
        if method != "GET":
            return method_not_allowed(("GET",))
        try:
            snapshot = await build_web_console_snapshot(
                service,
                session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
            )
        except ValueError as exc:
            return bad_request(str(exc))
        return text_response(
            render_web_doctor(snapshot, snapshot.doctor),
            content_type="text/html; charset=utf-8",
        )
    if path == "/console/distributed":
        if method != "GET":
            return method_not_allowed(("GET",))
        try:
            snapshot = await build_web_console_snapshot(
                service,
                session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
            )
        except ValueError as exc:
            return bad_request(str(exc))
        return text_response(
            render_web_distributed(
                snapshot,
                snapshot.distributed_snapshot,
                snapshot.distributed_health,
            ),
            content_type="text/html; charset=utf-8",
        )
    if path == "/console/multi-agent":
        if method != "GET":
            return method_not_allowed(("GET",))
        try:
            snapshot = await build_web_console_snapshot(
                service,
                session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
            )
        except ValueError as exc:
            return bad_request(str(exc))
        return text_response(
            render_web_multi_agent(snapshot),
            content_type="text/html; charset=utf-8",
        )
    console_explorer = _console_explorer_renderer(path)
    if console_explorer is not None:
        if method != "GET":
            return method_not_allowed(("GET",))
        is_world_explorer = path == "/console/world"
        try:
            snapshot = await build_web_console_snapshot(
                service,
                session_id=_optional_session_id_query(request.path),
                session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
                world_entity_id=(
                    _optional_query_value(request.path, "entity_id")
                    if is_world_explorer
                    else None
                ),
                world_relation=(
                    _optional_query_value(request.path, "relation")
                    if is_world_explorer
                    else None
                ),
            )
        except StateNotFoundError as exc:
            return not_found(str(exc))
        except ValueError as exc:
            return bad_request(str(exc))
        return text_response(
            console_explorer(snapshot),
            content_type="text/html; charset=utf-8",
        )
    console_catalog = _console_catalog_route(path)
    if console_catalog is not None:
        if method != "GET":
            return method_not_allowed(("GET",))
        try:
            snapshot = await build_web_console_snapshot(
                service,
                session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
            )
        except ValueError as exc:
            return bad_request(str(exc))
        return text_response(
            render_web_catalog(snapshot, console_catalog),
            content_type="text/html; charset=utf-8",
        )
    console_package_name, console_package_version = _console_domain_package_route(path)
    if console_package_name is not None:
        if method != "GET":
            return method_not_allowed(("GET",))
        try:
            snapshot = await build_web_console_snapshot(
                service,
                session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
            )
        except ValueError as exc:
            return bad_request(str(exc))
        package = _console_domain_package_view(
            snapshot,
            console_package_name,
            console_package_version,
        )
        if package is None:
            return not_found(
                _domain_package_not_found_message(
                    console_package_name,
                    console_package_version,
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
    console_domain_name, console_domain_version = _console_domain_route(path)
    if console_domain_name is not None:
        if method != "GET":
            return method_not_allowed(("GET",))
        try:
            snapshot = await build_web_console_snapshot(
                service,
                session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
            )
        except ValueError as exc:
            return bad_request(str(exc))
        domain = _console_domain_view(snapshot, console_domain_name, console_domain_version)
        if domain is None:
            return not_found(
                _domain_not_found_message(console_domain_name, console_domain_version)
            )
        return text_response(
            render_web_domain_detail(
                snapshot,
                domain_name=domain.name,
                domain_version=domain.version,
            ),
            content_type="text/html; charset=utf-8",
        )
    if path == "/console/sessions":
        if method != "GET":
            return method_not_allowed(("GET",))
        try:
            snapshot = await build_web_console_snapshot(
                service,
                session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
            )
        except ValueError as exc:
            return bad_request(str(exc))
        return text_response(
            render_web_sessions(snapshot),
            content_type="text/html; charset=utf-8",
        )
    console_session_id, console_session_suffix = _console_session_route(path)
    if console_session_id is not None:
        if method != "GET":
            return method_not_allowed(("GET",))
        is_world_explorer = console_session_suffix == "world"
        try:
            snapshot = await build_web_console_snapshot(
                service,
                session_id=console_session_id,
                session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
                world_entity_id=(
                    _optional_query_value(request.path, "entity_id")
                    if is_world_explorer
                    else None
                ),
                world_relation=(
                    _optional_query_value(request.path, "relation")
                    if is_world_explorer
                    else None
                ),
            )
        except StateNotFoundError as exc:
            return not_found(str(exc))
        except ValueError as exc:
            return bad_request(str(exc))
        renderer = _console_session_renderer(console_session_suffix)
        if renderer is None:
            return not_found(f"unknown route: {path}")
        return text_response(
            renderer(snapshot),
            content_type="text/html; charset=utf-8",
        )
    if path in ("/", "/console"):
        if method != "GET":
            return method_not_allowed(("GET",))
        try:
            snapshot = await build_web_console_snapshot(
                service,
                session_id=_optional_session_id_query(request.path),
                session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
            )
        except StateNotFoundError as exc:
            return not_found(str(exc))
        except ValueError as exc:
            return bad_request(str(exc))
        return text_response(
            render_web_console(snapshot),
            content_type="text/html; charset=utf-8",
        )
    return None
