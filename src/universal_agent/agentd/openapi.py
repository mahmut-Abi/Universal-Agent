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
        "version": "0.2.0",
        "description": (
            "Runtime-owned agentd API surface for sessions, domains, operations, "
            "and distributed coordination. The runtime owns all state; CLI, TUI "
            "and web surfaces are thin clients of this API."
        ),
    },
    "tags": [
        {"name": "System", "description": "Process health, readiness and configuration"},
        {"name": "Sessions", "description": "Goal runs, session lifecycle and projections"},
        {"name": "Catalog", "description": "Domains, packages, capabilities, tools, policies"},
        {"name": "Operations", "description": "Metrics, cost, logs, traces, doctor, audit"},
        {"name": "Distributed", "description": "Cross-node queue, locks and scheduling"},
    ],
}

_CREATED_ROUTES = frozenset({"sessions"})
_ACCEPTED_ROUTES = frozenset({"distributed_goals", "distributed_pending_actions_schedule"})
_TEXT_ROUTES = frozenset({"metrics_prometheus", "session_events_stream"})


# Route metadata: name -> (summary, description, tag). Routes missing from this
# table fall back to a name-derived summary with the generic tag.
_ROUTE_METADATA: dict[tuple[str, str] | str, tuple[str, str, str]] = {
    "health": ("Health check", "Liveness probe reporting process and service identity.", "System"),
    "ready": (
        "Readiness check",
        "Readiness probe aggregating domain, capability and tool availability.",
        "System",
    ),
    "config": (
        "Runtime configuration",
        "Active runtime configuration: environment, store, model and domain wiring.",
        "System",
    ),
    "metrics": (
        "Runtime metrics",
        "JSON runtime metrics: sessions, events, actions, policy and recovery counters.",
        "Operations",
    ),
    "metrics_prometheus": (
        "Prometheus metrics",
        "Prometheus exposition of the runtime metrics.",
        "Operations",
    ),
    "cost": (
        "Cost summary",
        "Model call, token and estimated cost accounting for all sessions.",
        "Operations",
    ),
    "logs": ("Runtime logs", "Structured runtime log records.", "Operations"),
    "traces": ("Trace spans", "Trace spans derived from runtime events.", "Operations"),
    "traces_otlp": (
        "Trace spans (OTLP JSON)",
        "Trace spans in OTLP-compatible JSON.",
        "Operations",
    ),
    "doctor": (
        "Doctor report",
        "Runtime health diagnostics: store, event and wiring checks.",
        "Operations",
    ),
    "audit": ("Audit records", "Operator audit trail records for all sessions.", "Operations"),
    "audit_integrity": (
        "Audit integrity",
        "Hash-chain integrity verification for the audit trail.",
        "Operations",
    ),
    "domains": (
        "List domains",
        "Active Domain Runtimes with ontology, capabilities and evaluators.",
        "Catalog",
    ),
    "domain_packages": (
        "List domain packages",
        "Installable Domain Packages with manifest and compatibility metadata.",
        "Catalog",
    ),
    "domain_package": ("Domain package detail", "A single Domain Package by name.", "Catalog"),
    "domain_package_version": (
        "Domain package version detail",
        "A specific Domain Package version.",
        "Catalog",
    ),
    "capabilities": (
        "List capabilities",
        "Registered capabilities with category, risk and side-effect metadata.",
        "Catalog",
    ),
    "tools": ("List tools", "Registered tools with schema and side-effect metadata.", "Catalog"),
    "policies": (
        "List policies",
        "Policy rules with effect, scope and risk thresholds.",
        "Catalog",
    ),
    "evaluators": (
        "List evaluators",
        "Registered evaluators with completion semantics.",
        "Catalog",
    ),
    "memory": (
        "List memories",
        "Memory records: semantic, episodic, procedural and preference kinds.",
        "Catalog",
    ),
    "profiles": ("List profiles", "Agent profiles with domain bindings.", "Catalog"),
    "profile": ("Profile detail", "A single Agent Profile by name.", "Catalog"),
    "multi_agent": (
        "Multi-agent registry",
        "Registered agents, delegation ledger and conflict state.",
        "Catalog",
    ),
    "sessions": (
        "List or create sessions",
        "List sessions (GET) or submit a goal to create and run a session (POST).",
        "Sessions",
    ),
    "session": (
        "Session detail",
        "Session aggregate: goal, task, status, criteria and domain identity.",
        "Sessions",
    ),
    "session_diagnostics": (
        "Session diagnostics",
        "Session diagnostics with evidence and evaluation projections.",
        "Sessions",
    ),
    "session_evidence": (
        "Session evidence",
        "Evidence records produced during the session.",
        "Sessions",
    ),
    "session_world": (
        "Session world model",
        "World facts, entities and relations projected from evidence.",
        "Sessions",
    ),
    "session_events": (
        "Session events",
        "Cursor-readable runtime events for the session.",
        "Sessions",
    ),
    "session_events_stream": (
        "Session event stream",
        "Server-sent event stream of runtime events (text/event-stream).",
        "Sessions",
    ),
    "session_audit": ("Session audit records", "Audit trail records for the session.", "Sessions"),
    "session_audit_integrity": (
        "Session audit integrity",
        "Hash-chain integrity verification for the session audit trail.",
        "Sessions",
    ),
    "session_cost": (
        "Session cost",
        "Model call, token and cost accounting for the session.",
        "Sessions",
    ),
    "session_logs": ("Session logs", "Runtime log records for the session.", "Sessions"),
    "session_traces": (
        "Session traces",
        "Trace spans derived from the session events.",
        "Sessions",
    ),
    "session_traces_otlp": (
        "Session traces (OTLP JSON)",
        "Session trace spans in OTLP-compatible JSON.",
        "Sessions",
    ),
    "session_pause": (
        "Pause session",
        "Gracefully pause the running session at the next boundary.",
        "Sessions",
    ),
    "session_resume": (
        "Resume session",
        "Resume a waiting or paused session; pending actions require explicit confirmation.",
        "Sessions",
    ),
    "session_cancel": (
        "Cancel session",
        "Cancel the session; tasks are marked cancelled.",
        "Sessions",
    ),
    "distributed_snapshot": (
        "Distributed snapshot",
        "Distributed runtime state: queue, workers, leases and locks.",
        "Distributed",
    ),
    "distributed_health": (
        "Distributed health",
        "Distributed runtime health report.",
        "Distributed",
    ),
    "distributed_worker_action": (
        "Distributed worker action",
        "Register, pause or drain a distributed worker.",
        "Distributed",
    ),
    "distributed_lock_acquire": (
        "Acquire distributed lock",
        "Acquire a leased lock with fencing tokens.",
        "Distributed",
    ),
    "distributed_lock_lease_action": (
        "Distributed lock lease action",
        "Release, renew or revoke a distributed lock lease.",
        "Distributed",
    ),
    "distributed_goals": (
        "Schedule distributed goal",
        "Enqueue a goal for execution by the distributed worker pool.",
        "Distributed",
    ),
    "distributed_pending_actions_schedule": (
        "Schedule pending actions",
        "Schedule pending actions onto the distributed queue.",
        "Distributed",
    ),
    "distributed_schedule_action": (
        "Schedule action",
        "Schedule a single action for distributed execution.",
        "Distributed",
    ),
    "distributed_schedule_task": (
        "Schedule task",
        "Schedule a task for distributed execution.",
        "Distributed",
    ),
    "distributed_schedule_session": (
        "Schedule session",
        "Schedule a session for distributed execution.",
        "Distributed",
    ),
    "distributed_expire": (
        "Expire leases",
        "Expire stale leases and requeue their work.",
        "Distributed",
    ),
    "distributed_prune_terminal": (
        "Prune terminal records",
        "Prune terminal work items and records past retention.",
        "Distributed",
    ),
}


_REQUEST_SCHEMAS: dict[str, dict[str, Any]] = {
    "sessions": {
        "type": "object",
        "required": ["goal"],
        "properties": {
            "goal": {
                "type": "object",
                "required": ["description"],
                "properties": {
                    "description": {"type": "string"},
                    "success_criteria": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["key"],
                            "properties": {
                                "key": {"type": "string"},
                                "expected": {},
                            },
                        },
                    },
                },
            },
            "task": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "required_criteria": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
    "memory_create": {
        "type": "object",
        "required": ["kind", "subject", "content"],
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["semantic", "episodic", "procedural", "preference"],
                "description": (
                    "Memory kind; operator-managed kinds are semantic/procedural/preference"
                ),
            },
            "subject": {"type": "string", "minLength": 1},
            "content": {"type": "string", "minLength": 1},
            "scope": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    },
    "session_pause": {
        "type": "object",
        "properties": {"reason": {"type": "string", "description": "Why the session is paused"}},
    },
    "session_resume": {
        "type": "object",
        "properties": {
            "confirmed": {
                "type": "boolean",
                "description": (
                    "Required (true) to execute a pending action that policy held "
                    "for confirmation; false rejects it"
                ),
            }
        },
    },
    "session_cancel": {
        "type": "object",
        "properties": {"reason": {"type": "string", "description": "Why the session is cancelled"}},
    },
    "session_events": {
        "type": "object",
        "properties": {
            "after_event_id": {"type": "string"},
            "limit": {"type": "integer"},
            "wait": {"type": "boolean"},
        },
    },
}


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
    summary, description, tag = _ROUTE_METADATA.get(
        (route.name, method.upper())
    ) or _ROUTE_METADATA.get(route.name, (_route_summary(route.name), "", "System"))
    operation: dict[str, Any] = {
        "operationId": _operation_id(route.name, method),
        "summary": summary,
        "tags": [tag],
        "responses": {
            _success_status(route.name, method): {
                "description": _success_description(route.name),
                "content": _response_content(route.name),
            }
        },
    }
    if description:
        operation["description"] = description
    if method.upper() == "GET" and route.name in _REQUEST_SCHEMAS:
        operation["parameters"] = [
            {"name": k, "in": "query", "schema": schema}
            for k, schema in _REQUEST_SCHEMAS[route.name].get("properties", {}).items()
        ]
    if method.upper() != "GET":
        request_schema = _REQUEST_SCHEMAS.get(route.name, {"type": "object"})
        operation["requestBody"] = {
            "required": route.name in {"sessions", "session_resume"},
            "content": {"application/json": {"schema": request_schema}},
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
