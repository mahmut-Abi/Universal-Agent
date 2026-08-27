from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from universal_agent.core import (
    Goal,
    JsonMapping,
    JsonValue,
    SuccessCriterion,
    Task,
    immutable_json,
)


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
    text_body: str | None = None


@dataclass(frozen=True, slots=True)
class GoalSubmission:
    goal: Goal
    task: Task
    profile_name: str | None = None


@dataclass(frozen=True, slots=True)
class AgentdAuthPolicy:
    bearer_token: str | None = None
    read_only_bearer_token: str | None = None
    public_paths: tuple[str, ...] = ("/health", "/ready")

    def __post_init__(self) -> None:
        _validate_bearer_token(self.bearer_token, "agentd bearer token")
        _validate_bearer_token(
            self.read_only_bearer_token,
            "agentd read-only bearer token",
        )
        if (
            self.bearer_token is not None
            and self.read_only_bearer_token is not None
            and hmac.compare_digest(self.bearer_token, self.read_only_bearer_token)
        ):
            raise ValueError("agentd bearer token and read-only bearer token must differ")
        if any(not path.startswith("/") or not path.strip() for path in self.public_paths):
            raise ValueError("agentd public paths must be absolute non-empty paths")

    @property
    def enabled(self) -> bool:
        return self.bearer_token is not None or self.read_only_bearer_token is not None


def json_response(body: JsonMapping, *, status_code: int = 200) -> HttpResponse:
    return HttpResponse(status_code=status_code, body=body)


def text_response(
    text_body: str,
    *,
    status_code: int = 200,
    content_type: str = "text/plain; charset=utf-8",
) -> HttpResponse:
    return HttpResponse(
        status_code=status_code,
        body=immutable_json(),
        headers=MappingProxyType({"content-type": content_type}),
        text_body=text_body,
    )


def not_found(message: str) -> HttpResponse:
    return json_response(error_body("not_found", message), status_code=404)


def unauthorized() -> HttpResponse:
    return HttpResponse(
        status_code=401,
        body=error_body("unauthorized", "authentication required"),
        headers=MappingProxyType(
            {
                "content-type": "application/json",
                "www-authenticate": 'Bearer realm="agentd"',
            }
        ),
    )


def forbidden(message: str) -> HttpResponse:
    return json_response(error_body("forbidden", message), status_code=403)


def bad_request(message: str) -> HttpResponse:
    return json_response(error_body("bad_request", message), status_code=400)


def conflict(message: str) -> HttpResponse:
    return json_response(error_body("conflict", message), status_code=409)


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


def _authenticate(
    policy: AgentdAuthPolicy,
    request: HttpRequest,
    path: str,
    *,
    method: str,
) -> HttpResponse | None:
    if not policy.enabled or path in policy.public_paths:
        return None
    token = _bearer_token(_header_value(request.headers, "authorization"))
    if token is None:
        return unauthorized()
    if _token_matches(token, policy.bearer_token):
        return None
    if _token_matches(token, policy.read_only_bearer_token):
        if method == "GET":
            return None
        return forbidden("insufficient bearer token scope")
    return unauthorized()


def _validate_bearer_token(value: str | None, field: str) -> None:
    if value is not None and not value.strip():
        raise ValueError(f"{field} must not be empty")


def _token_matches(token: str, expected: str | None) -> bool:
    if expected is None:
        return False
    return hmac.compare_digest(token, expected)


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    normalized = name.lower()
    for key, value in headers.items():
        if key.lower() == normalized:
            return value
    return None


def _bearer_token(value: str | None) -> str | None:
    if value is None:
        return None
    scheme, separator, token = value.strip().partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        return None
    return token


def parse_goal_submission(body: JsonMapping) -> GoalSubmission:
    profile_name = _optional_non_empty_string_field(body, "profile", "profile")
    goal_payload = _object_field(body, "goal", "goal")
    task_payload = _object_field(body, "task", "task")
    goal = Goal(
        _non_empty_string_field(goal_payload, "description", "goal.description"),
        _success_criteria(goal_payload),
    )
    task = Task(
        _non_empty_string_field(task_payload, "description", "task.description"),
        _string_tuple_field(task_payload, "required_criteria", "task.required_criteria"),
    )
    return GoalSubmission(goal, task, profile_name)


def _success_criteria(payload: Mapping[str, JsonValue]) -> tuple[SuccessCriterion, ...]:
    items = _list_field(payload, "success_criteria", "goal.success_criteria")
    if not items:
        raise ValueError("goal.success_criteria must not be empty")
    criteria: list[SuccessCriterion] = []
    for index, item in enumerate(items):
        field = f"goal.success_criteria[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{field} must be an object")
        criteria.append(
            SuccessCriterion(
                _non_empty_string_field(item, "key", f"{field}.key"),
                _required_field(item, "expected", f"{field}.expected"),
            )
        )
    return tuple(criteria)


def _string_tuple_field(
    payload: Mapping[str, JsonValue],
    key: str,
    field: str,
) -> tuple[str, ...]:
    items = _list_field(payload, key, field)
    values: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, str):
            raise ValueError(f"{field}[{index}] must be a string")
        if not item.strip():
            raise ValueError(f"{field}[{index}] must not be empty")
        values.append(item)
    return tuple(values)


def _object_field(
    payload: Mapping[str, JsonValue], key: str, field: str
) -> Mapping[str, JsonValue]:
    value = _required_field(payload, key, field)
    if isinstance(value, dict):
        return value
    raise ValueError(f"{field} must be an object")


def _optional_datetime_field(
    payload: Mapping[str, JsonValue],
    key: str,
    field: str,
) -> datetime | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO 8601 datetime string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO 8601 datetime string") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed


def _list_field(payload: Mapping[str, JsonValue], key: str, field: str) -> list[JsonValue]:
    value = _required_field(payload, key, field)
    if isinstance(value, list):
        return value
    raise ValueError(f"{field} must be a list")


def _non_empty_string_field(payload: Mapping[str, JsonValue], key: str, field: str) -> str:
    value = _required_field(payload, key, field)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value


def _optional_non_empty_string_field(
    payload: Mapping[str, JsonValue],
    key: str,
    field: str,
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value


def _required_field(payload: Mapping[str, JsonValue], key: str, field: str) -> JsonValue:
    try:
        return payload[key]
    except KeyError as exc:
        raise ValueError(f"{field} is required") from exc
