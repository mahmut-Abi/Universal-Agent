from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from urllib.parse import urlsplit

from universal_agent.core import ExecutionResult, JsonMapping, JsonValue, SessionId, immutable_json
from universal_agent.runtime import (
    EvaluationView,
    PendingActionView,
    RuntimeEventView,
    RuntimeRun,
    SessionView,
    TaskView,
)
from universal_agent.service import (
    CapabilityView,
    DomainView,
    HealthView,
    ReadyView,
    RuntimeService,
    ToolView,
)
from universal_agent.state import StateNotFoundError


def _empty_json() -> JsonMapping:
    return immutable_json()


def _default_headers() -> Mapping[str, str]:
    return MappingProxyType({"content-type": "application/json"})


@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: str
    path: str
    body: JsonMapping = field(default_factory=_empty_json)
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: JsonMapping
    headers: Mapping[str, str] = field(default_factory=_default_headers)


class AgentdApp:
    """Framework-free route adapter for the future agentd process.

    It owns HTTP-shaped routing and JSON serialization. Runtime behavior stays
    behind RuntimeService, so a real socket server can later wrap this adapter
    without learning Kernel internals.
    """

    def __init__(self, service: RuntimeService) -> None:
        self._service = service

    async def handle(self, request: HttpRequest) -> HttpResponse:
        method = request.method.upper()
        path = _normalize_path(request.path)

        if path == "/health":
            return self._get(method, health_body(self._service.health()))
        if path == "/ready":
            return self._get(method, ready_body(self._service.ready()))
        if path == "/v1/domains":
            return self._get(
                method,
                immutable_json(
                    {"domains": [domain_body(item) for item in self._service.domains()]}
                ),
            )
        if path == "/v1/capabilities":
            return self._get(
                method,
                immutable_json(
                    {
                        "capabilities": [
                            capability_body(item) for item in self._service.capabilities()
                        ]
                    }
                ),
            )
        if path == "/v1/tools":
            return self._get(
                method,
                immutable_json({"tools": [tool_body(item) for item in self._service.tools()]}),
            )

        session_id, suffix = _session_route(path)
        if session_id is not None and suffix == "":
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                return json_response(session_body(await self._service.get_session(session_id)))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if session_id is not None and suffix == "events":
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                events = await self._service.list_events(session_id)
                return json_response(
                    immutable_json({"events": [event_body(item) for item in events]})
                )
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if session_id is not None and suffix == "resume":
            if method != "POST":
                return method_not_allowed(("POST",))
            confirmed = request.body.get("confirmed")
            if not isinstance(confirmed, bool):
                return bad_request("resume requires boolean confirmed")
            try:
                run = await self._service.resume_session(session_id, confirmed=confirmed)
                return json_response(runtime_run_body(run))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if session_id is not None and suffix == "cancel":
            if method != "POST":
                return method_not_allowed(("POST",))
            reason = request.body.get("reason", "session cancelled")
            if not isinstance(reason, str):
                return bad_request("cancel reason must be a string")
            try:
                run = await self._service.cancel_session(session_id, reason=reason)
                return json_response(runtime_run_body(run))
            except StateNotFoundError as exc:
                return not_found(str(exc))

        return not_found(f"unknown route: {path}")

    def _get(self, method: str, body: JsonMapping) -> HttpResponse:
        if method != "GET":
            return method_not_allowed(("GET",))
        return json_response(body)


def json_response(body: JsonMapping, *, status_code: int = 200) -> HttpResponse:
    return HttpResponse(status_code=status_code, body=body)


def not_found(message: str) -> HttpResponse:
    return json_response(error_body("not_found", message), status_code=404)


def bad_request(message: str) -> HttpResponse:
    return json_response(error_body("bad_request", message), status_code=400)


def method_not_allowed(allowed: tuple[str, ...]) -> HttpResponse:
    headers = MappingProxyType(
        {
            "content-type": "application/json",
            "allow": ", ".join(allowed),
        }
    )
    return HttpResponse(
        status_code=405,
        body=error_body("method_not_allowed", "method is not allowed for this route"),
        headers=headers,
    )


def error_body(code: str, message: str) -> JsonMapping:
    return immutable_json({"error": {"code": code, "message": message}})


def runtime_run_body(run: RuntimeRun) -> JsonMapping:
    return immutable_json(
        {
            "result": execution_result_body(run.result),
            "session": dict(session_body(run.session)),
        }
    )


def execution_result_body(result: ExecutionResult) -> dict[str, JsonValue]:
    return {
        "status": result.status.value,
        "session_id": str(result.session_id),
        "goal_id": str(result.goal_id),
        "task_id": str(result.task_id),
        "iterations": result.iterations,
        "reason": result.reason,
        "error_code": result.error_code.value if result.error_code is not None else None,
        "user_message": result.user_message,
    }


def health_body(view: HealthView) -> JsonMapping:
    return immutable_json({"status": view.status, "service": view.service})


def ready_body(view: ReadyView) -> JsonMapping:
    return immutable_json(
        {
            "ready": view.ready,
            "reason": view.reason,
            "domain_count": view.domain_count,
            "capability_count": view.capability_count,
            "tool_count": view.tool_count,
        }
    )


def domain_body(view: DomainView) -> dict[str, JsonValue]:
    return {
        "name": view.name,
        "version": view.version,
        "description": view.description,
        "primary": view.primary,
        "ontology": list(view.ontology),
        "capability_names": list(view.capability_names),
        "evaluator_names": list(view.evaluator_names),
    }


def capability_body(view: CapabilityView) -> dict[str, JsonValue]:
    return {
        "name": view.name,
        "description": view.description,
        "category": view.category.value,
        "risk": view.risk.value,
        "domain_name": view.domain_name,
        "domain_version": view.domain_version,
        "tool_names": list(view.tool_names),
    }


def tool_body(view: ToolView) -> dict[str, JsonValue]:
    return {
        "name": view.name,
        "description": view.description,
        "capabilities": list(view.capabilities),
        "required_arguments": list(view.required_arguments),
        "side_effect": view.side_effect.value,
        "risk": view.risk.value,
        "timeout_seconds": view.timeout_seconds,
        "priority": view.priority,
        "domain_name": view.domain_name,
        "domain_version": view.domain_version,
    }


def session_body(view: SessionView) -> JsonMapping:
    return immutable_json(
        {
            "session_id": str(view.session_id),
            "goal_id": str(view.goal_id),
            "goal_description": view.goal_description,
            "goal_status": view.goal_status.value,
            "current_task_id": str(view.current_task_id),
            "current_task_description": view.current_task_description,
            "current_task_status": view.current_task_status.value,
            "iteration": view.iteration,
            "tasks": [task_body(item) for item in view.tasks],
            "satisfied_criteria": _json_value(view.satisfied_criteria),
            "pending_action": pending_action_body(view.pending_action),
            "latest_evaluation": evaluation_body(view.latest_evaluation),
            "termination_reason": view.termination_reason,
            "error_code": view.error_code.value if view.error_code is not None else None,
            "domain_name": view.domain_name,
            "domain_version": view.domain_version,
        }
    )


def task_body(view: TaskView) -> dict[str, JsonValue]:
    return {
        "task_id": str(view.task_id),
        "description": view.description,
        "status": view.status.value,
        "required_criteria": list(view.required_criteria),
        "depends_on": [str(item) for item in view.depends_on],
    }


def pending_action_body(view: PendingActionView | None) -> JsonValue:
    if view is None:
        return None
    return {
        "action_id": str(view.action_id),
        "capability": view.capability,
        "tool_name": view.tool_name,
        "target": view.target,
        "arguments": _json_value(view.arguments),
        "domain_name": view.domain_name,
        "domain_version": view.domain_version,
    }


def evaluation_body(view: EvaluationView | None) -> JsonValue:
    if view is None:
        return None
    return {
        "status": view.status.value,
        "reason": view.reason,
        "evaluator_name": view.evaluator_name,
        "matched_criteria": _json_value(view.matched_criteria),
        "task_completed": view.task_completed,
        "goal_completed": view.goal_completed,
    }


def event_body(view: RuntimeEventView) -> dict[str, JsonValue]:
    return {
        "event_id": view.event_id,
        "type": view.type,
        "session_id": str(view.session_id),
        "goal_id": str(view.goal_id),
        "task_id": str(view.task_id),
        "action_id": str(view.action_id) if view.action_id is not None else None,
        "data": _json_value(view.data),
        "occurred_at": view.occurred_at.isoformat(),
    }


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_value(item) for item in value]
    return str(value)


def _normalize_path(path: str) -> str:
    normalized = urlsplit(path).path
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    normalized = normalized.rstrip("/")
    return normalized or "/"


def _session_route(path: str) -> tuple[SessionId | None, str]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if len(segments) == 3 and segments[:2] == ("v1", "sessions"):
        return SessionId(segments[2]), ""
    if len(segments) == 4 and segments[:2] == ("v1", "sessions"):
        return SessionId(segments[2]), segments[3]
    return None, ""
