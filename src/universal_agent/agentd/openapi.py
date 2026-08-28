from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import yaml
from starlette.routing import BaseRoute, Route
from starlette.schemas import SchemaGenerator

from universal_agent.agentd.routing import AgentdRouteDefinition
from universal_agent.core import JsonMapping, immutable_json
from universal_agent.core.config_validation import parse_json_object

_BASE_SCHEMA: dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {
        "title": "Universal Agent Runtime API",
        "version": "0.1.0",
        "description": (
            "Runtime-owned agentd API surface for sessions, domains, operations, "
            "and distributed coordination."
        ),
    },
}

_CREATED_ROUTES = frozenset({"sessions"})
_ACCEPTED_ROUTES = frozenset({"distributed_goals", "distributed_pending_actions_schedule"})
_TEXT_ROUTES = frozenset({"metrics_prometheus", "session_events_stream"})


def build_agentd_openapi_schema(
    route_definitions: Iterable[AgentdRouteDefinition],
) -> JsonMapping:
    """Generate the agentd OpenAPI document from existing Starlette route primitives."""

    generator = SchemaGenerator(_BASE_SCHEMA)
    schema = generator.get_schema(_schema_routes(route_definitions))
    paths = schema.get("paths", {})
    if isinstance(paths, dict):
        schema["paths"] = dict(sorted(paths.items()))
    return immutable_json(parse_json_object(schema, "agentd openapi schema"))


def _schema_routes(route_definitions: Iterable[AgentdRouteDefinition]) -> list[BaseRoute]:
    routes: list[BaseRoute] = []
    for route in route_definitions:
        routes.extend(
            Route(
                route.template,
                _schema_endpoint(route, method),
                methods=[method],
                name=f"{route.name}_{method.lower()}",
            )
            for method in route.methods
        )
    return routes


def _schema_endpoint(
    route: AgentdRouteDefinition,
    method: str,
) -> Callable[..., None]:
    def endpoint(*_args: object, **_kwargs: object) -> None:
        return None

    endpoint.__name__ = route.name
    endpoint.__doc__ = _route_docstring(route, method)
    return endpoint


def _route_docstring(route: AgentdRouteDefinition, method: str) -> str:
    operation = {
        "operationId": _operation_id(route.name, method),
        "summary": _route_summary(route.name),
        "responses": {
            _success_status(route.name, method): {
                "description": _success_description(route.name),
                "content": _response_content(route.name),
            }
        },
    }
    if method.upper() != "GET":
        operation["requestBody"] = {
            "required": False,
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                }
            },
        }
    return "---\n" + yaml.safe_dump(operation, sort_keys=False)


def _operation_id(route_name: str, method: str) -> str:
    if method.upper() == "GET":
        return route_name
    return f"{route_name}_{method.lower()}"


def _success_status(route_name: str, method: str) -> str:
    if method.upper() == "POST" and route_name in _CREATED_ROUTES:
        return "201"
    if method.upper() == "POST" and route_name in _ACCEPTED_ROUTES:
        return "202"
    return "200"


def _route_summary(route_name: str) -> str:
    return route_name.replace("_", " ").capitalize()


def _success_description(route_name: str) -> str:
    if route_name in _TEXT_ROUTES:
        return "Text response"
    return "JSON response"


def _response_content(route_name: str) -> dict[str, dict[str, Any]]:
    if route_name == "metrics_prometheus":
        return {"text/plain": {"schema": {"type": "string"}}}
    if route_name == "session_events_stream":
        return {"text/event-stream": {"schema": {"type": "string"}}}
    return {"application/json": {"schema": {"type": "object"}}}


__all__ = ["build_agentd_openapi_schema"]
