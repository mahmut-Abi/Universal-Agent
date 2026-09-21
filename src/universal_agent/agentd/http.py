from __future__ import annotations

import hmac
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError
from starlette.datastructures import Headers

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
    PydanticNonEmptyString,
    parse_non_empty_string_sequence,
    parse_optional_non_empty_string,
    parse_optional_string,
    pydantic_error_message,
)
from universal_agent.security import (
    AuditEvent,
    AuditRecorder,
    AuthorizationEvaluator,
    CredentialAdminStore,
    RequestPrincipal,
)


def _empty_json() -> JsonMapping:
    return immutable_json()


def _default_headers() -> Mapping[str, str]:
    return MappingProxyType({"content-type": "application/json"})


_EVALUATOR = AuthorizationEvaluator()


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
    stream_body: AsyncIterator[str] | None = None


@dataclass(frozen=True, slots=True)
class GoalSubmission:
    goal: Goal
    task: Task | None
    profile_name: str | None = None
    compile_goal: bool = False
    timeout_seconds: float | None = None
    read_only: bool = False


class _SuccessCriterionPayload(ConfigPayload):
    key: PydanticNonEmptyString
    expected: PydanticJsonValue


class _GoalPayload(ConfigPayload):
    description: PydanticNonEmptyString
    success_criteria: list[_SuccessCriterionPayload] = Field(default_factory=list)


class _TaskPayload(ConfigPayload):
    description: PydanticNonEmptyString
    required_criteria: list[PydanticNonEmptyString]


class _GoalSubmissionPayload(ConfigPayload):
    goal: _GoalPayload
    task: _TaskPayload | None = None
    profile: PydanticNonEmptyString | None = None
    compile_goal: bool = False
    timeout_seconds: float | None = Field(default=None, gt=0)
    read_only: bool = False


@dataclass(frozen=True, slots=True)
class AgentdAuthPolicy:
    bearer_token: str | None = None
    read_only_bearer_token: str | None = None
    public_paths: tuple[str, ...] = ("/health", "/ready")
    # When set, bearer tokens are resolved through this CredentialStore to a
    # RequestPrincipal, and role/scope (and tenant) gate each request. Falls
    # back to the legacy shared-token model when absent.
    credential_store: CredentialAdminStore | None = None
    # The tenant this agentd process is scoped to (the store's tenant). When a
    # credential resolves to a different tenant, the request is refused even if
    # the credential itself is valid (cross-tenant denial at the agentd layer).
    tenant_id: str | None = None

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
        if self.tenant_id is not None:
            parse_optional_non_empty_string(self.tenant_id, "agentd tenant_id")
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
        return (
            self.bearer_token is not None
            or self.read_only_bearer_token is not None
            or self.credential_store is not None
        )


def json_response(body: JsonMapping, *, status_code: int = 200) -> HttpResponse:
    return HttpResponse(status_code=status_code, body=body)


def see_other(location: str) -> HttpResponse:
    """303 redirect used by console action forms (POST-redirect-GET)."""

    return HttpResponse(
        status_code=303,
        body=immutable_json(),
        headers=MappingProxyType({"location": location}),
    )


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


@dataclass(frozen=True, slots=True)
class AuthOutcome:
    """Result of authenticating a request: an optional refusal response plus
    the resolved principal (when the credential store produced one).

    ``principal is None`` means either the request was public, or legacy
    shared-token authentication matched — legacy tokens keep full (admin-equivalent)
    access for backward compatibility until credentials are provisioned.
    """

    response: HttpResponse | None = None
    principal: RequestPrincipal | None = None


def _authenticate(
    policy: AgentdAuthPolicy,
    request: HttpRequest,
    path: str,
    *,
    method: str,
    audit: AuditRecorder | None = None,
) -> AuthOutcome:
    def _record_denial(reason: str, actor: str = "anonymous") -> None:
        if audit is not None:
            audit.record(
                AuditEvent(
                    event="denied",
                    actor=actor,
                    reason=reason,
                    resource=path,
                    details={"method": method},
                )
            )

    if not policy.enabled or path in policy.public_paths:
        return AuthOutcome()
    token = _bearer_token(_authorization_header(request.headers))
    if token is None:
        _record_denial("unauthorized")
        return AuthOutcome(response=unauthorized())
    # Principal-aware path: resolve the bearer token to a RequestPrincipal and
    # enforce role/scope + cross-tenant denial (RBAC). Kept on the legacy
    # shared-token model when no credential store is configured.
    if policy.credential_store is not None:
        principal = policy.credential_store.resolve(token)
        if principal is None:
            # Fresh-install bootstrap: while no admin membership exists, the
            # legacy shared bearer token keeps full access so the first admin
            # can be provisioned. As soon as an admin exists it is rejected
            # like any other unknown token.
            if _token_matches(token, policy.bearer_token) and not (
                policy.credential_store.has_any_admin()
                and policy.credential_store.has_any_credential()
            ):
                # Bootstrap window: open while no usable admin credential
                # exists, so issuing the credential AFTER a role assignment
                # is still possible (UA-LIVE-2026-09-21 Q12).
                return AuthOutcome()
            _record_denial("unauthorized")
            return AuthOutcome(response=unauthorized())
        if policy.tenant_id is not None and principal.tenant_id != policy.tenant_id:
            _record_denial("cross_tenant", actor=principal.subject)
            return AuthOutcome(
                response=forbidden(
                    f"cross_tenant: subject {principal.subject} is not a member of "
                    f"the server tenant {policy.tenant_id}"
                )
            )
        decision = _EVALUATOR.authorize_request(principal, method=method)
        if not decision.allowed:
            _record_denial("rbac", actor=principal.subject)
            return AuthOutcome(response=forbidden(f"rbac: {decision.message}"))
        return AuthOutcome(principal=principal)
    if _token_matches(token, policy.bearer_token):
        return AuthOutcome()
    if _token_matches(token, policy.read_only_bearer_token):
        if method == "GET":
            return AuthOutcome()
        return AuthOutcome(response=forbidden("insufficient bearer token scope"))
    _record_denial("unauthorized")
    return AuthOutcome(response=unauthorized())


def _validate_bearer_token(value: str | None, field: str) -> None:
    parse_optional_non_empty_string(value, field)


def _token_matches(token: str, expected: str | None) -> bool:
    if expected is None:
        return False
    return hmac.compare_digest(token, expected)


def _authorization_header(headers: Mapping[str, str]) -> str | None:
    return Headers(headers=headers).get("authorization")


def _bearer_token(value: str | None) -> str | None:
    if value is None:
        return None
    scheme, separator, token = value.strip().partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        return None
    return token


def parse_goal_submission(body: JsonMapping) -> GoalSubmission:
    payload = _parse_goal_submission_payload(body)
    goal_payload = payload.goal
    task_payload = payload.task
    goal = Goal(goal_payload.description, _success_criteria(goal_payload.success_criteria))
    if payload.compile_goal:
        if task_payload is not None:
            raise ValueError("task must be omitted when compile_goal is true")
        return GoalSubmission(
            goal, None, payload.profile, True, payload.timeout_seconds, payload.read_only
        )
    if task_payload is None:
        raise ValueError("task is required")
    task = Task(task_payload.description, tuple(task_payload.required_criteria))
    return GoalSubmission(
        goal, task, payload.profile, False, payload.timeout_seconds, payload.read_only
    )


def _parse_goal_submission_payload(body: JsonMapping) -> _GoalSubmissionPayload:
    try:
        return _GoalSubmissionPayload.model_validate(dict(body))
    except PydanticValidationError as exc:
        raise ValueError(_goal_submission_payload_error_message(exc)) from exc


def _goal_submission_payload_error_message(error: PydanticValidationError) -> str:
    return pydantic_error_message(error, missing_template="{path} is required")


def _success_criteria(
    items: list[_SuccessCriterionPayload],
) -> tuple[SuccessCriterion, ...]:
    return tuple(SuccessCriterion(item.key, item.expected) for item in items)


def _optional_datetime_field(
    payload: Mapping[str, JsonValue],
    key: str,
    field: str,
) -> datetime | None:
    value = parse_optional_string(payload.get(key), field)
    if value is None:
        return None
    return parse_iso_datetime(
        value,
        field=field,
        description="an ISO 8601 datetime string",
        require_timezone=True,
    )
