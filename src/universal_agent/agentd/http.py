from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from pydantic import field_validator

from universal_agent.core import (
    Goal,
    JsonMapping,
    JsonValue,
    SuccessCriterion,
    Task,
    immutable_json,
    parse_iso_datetime,
)
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    parse_non_empty_string,
    parse_non_empty_string_sequence,
    parse_optional_non_empty_string,
    parse_payload,
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


class _SuccessCriterionPayload(ConfigPayload):
    key: str
    expected: PydanticJsonValue


class _GoalPayload(ConfigPayload):
    description: str
    success_criteria: list[_SuccessCriterionPayload]

    @field_validator("success_criteria")
    @classmethod
    def _require_success_criteria(
        cls,
        value: list[_SuccessCriterionPayload],
    ) -> list[_SuccessCriterionPayload]:
        if not value:
            raise ValueError("goal.success_criteria must not be empty")
        return value


class _TaskPayload(ConfigPayload):
    description: str
    required_criteria: list[str]


class _GoalSubmissionPayload(ConfigPayload):
    goal: _GoalPayload
    task: _TaskPayload
    profile: str | None = None


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
        public_paths = parse_non_empty_string_sequence(
            self.public_paths,
            "agentd public paths",
            empty_template="agentd public paths must be absolute non-empty paths",
            item_type_template="agentd public paths must be absolute non-empty paths",
        )
        if any(not path.startswith("/") for path in public_paths):
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
    parse_optional_non_empty_string(value, field)


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
    payload = _parse_goal_submission_payload(body)
    profile_name = parse_optional_non_empty_string(payload.profile, "profile")
    goal_payload = payload.goal
    task_payload = payload.task
    goal = Goal(
        parse_non_empty_string(goal_payload.description, "goal.description"),
        _success_criteria(goal_payload.success_criteria),
    )
    task = Task(
        parse_non_empty_string(task_payload.description, "task.description"),
        parse_non_empty_string_sequence(
            task_payload.required_criteria,
            "task.required_criteria",
        ),
    )
    return GoalSubmission(goal, task, profile_name)


def _parse_goal_submission_payload(body: JsonMapping) -> _GoalSubmissionPayload:
    return parse_payload(_GoalSubmissionPayload, body, missing_template="{path} is required")


def _success_criteria(
    items: list[_SuccessCriterionPayload],
) -> tuple[SuccessCriterion, ...]:
    criteria: list[SuccessCriterion] = []
    for index, item in enumerate(items):
        criteria.append(
            SuccessCriterion(
                parse_non_empty_string(item.key, f"goal.success_criteria[{index}].key"),
                item.expected,
            )
        )
    return tuple(criteria)


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
    return parse_iso_datetime(
        value,
        field=field,
        description="an ISO 8601 datetime string",
        require_timezone=True,
    )
