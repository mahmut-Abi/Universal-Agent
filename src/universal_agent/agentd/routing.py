from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError
from starlette.datastructures import URL, QueryParams
from starlette.routing import Match, Route

from universal_agent.core import EventId, JsonMapping, SessionId
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    PydanticNonEmptyString,
    json_mapping,
    parse_bool_text,
    parse_bounded_float_text,
    parse_non_empty_string,
    parse_positive_int_text,
    pydantic_error_details,
)
from universal_agent.distributed import DistributedLockOwnerId
from universal_agent.service import DomainPackageView, DomainView
from universal_agent.web import WebConsoleSnapshot


@dataclass(frozen=True, slots=True)
class AgentdRouteDefinition:
    name: str
    template: str
    methods: tuple[str, ...] = ("GET",)


@dataclass(frozen=True, slots=True)
class AgentdRouteMatch:
    name: str
    path_params: Mapping[str, str]
    allowed_methods: tuple[str, ...]
    method_allowed: bool


class AgentdRouteMatcher:
    """Route matcher backed by Starlette path templates.

    AgentdApp still owns runtime behavior. This module owns HTTP route matching
    semantics so path parsing stays at the HTTP adapter seam.
    """

    def __init__(self, routes: tuple[AgentdRouteDefinition, ...]) -> None:
        self._routes = routes

    def match(self, path: str, method: str) -> AgentdRouteMatch | None:
        request_method = method.upper()
        for route in self._routes:
            params = _match_path(path, route.template)
            if params is None:
                continue
            allowed_methods = tuple(item.upper() for item in route.methods)
            return AgentdRouteMatch(
                route.name,
                params,
                allowed_methods,
                request_method in allowed_methods,
            )
        return None


class _DistributedLockAcquirePayload(ConfigPayload):
    lock_key: PydanticNonEmptyString
    owner_id: PydanticNonEmptyString
    ttl_seconds: float = Field(default=30.0, gt=0)
    metadata: dict[str, PydanticJsonValue] | None = None

    @property
    def lock_owner_id(self) -> DistributedLockOwnerId:
        return DistributedLockOwnerId(self.owner_id)

    @property
    def metadata_mapping(self) -> JsonMapping | None:
        if self.metadata is None:
            return None
        return json_mapping(self.metadata)


class _DistributedLockLeasePayload(ConfigPayload):
    owner_id: PydanticNonEmptyString
    ttl_seconds: float = Field(default=30.0, gt=0)

    @property
    def lock_owner_id(self) -> DistributedLockOwnerId:
        return DistributedLockOwnerId(self.owner_id)


class _DistributedWorkerRegistrationPayload(ConfigPayload):
    capabilities: list[PydanticNonEmptyString] = Field(default_factory=list)
    metadata: dict[str, PydanticJsonValue] | None = None
    ttl_seconds: float = Field(default=30.0, gt=0)

    @property
    def capability_tuple(self) -> tuple[str, ...]:
        return tuple(self.capabilities)

    @property
    def metadata_mapping(self) -> JsonMapping | None:
        if self.metadata is None:
            return None
        return json_mapping(self.metadata)


class _DistributedWorkerTtlPayload(ConfigPayload):
    ttl_seconds: float = Field(default=30.0, gt=0)


class _DistributedWorkerRunPayload(ConfigPayload):
    lease_ttl_seconds: float = Field(default=30.0, gt=0)
    worker_ttl_seconds: float = Field(default=30.0, gt=0)
    heartbeat_interval_seconds: float | None = Field(default=None, gt=0)


class _DistributedWorkerRunBatchPayload(_DistributedWorkerRunPayload):
    max_items: int = Field(default=1, ge=1)


class _DistributedSchedulePayload(ConfigPayload):
    payload: dict[str, PydanticJsonValue] | None = None
    priority: int = 0
    max_attempts: int = 3

    @property
    def payload_mapping(self) -> JsonMapping | None:
        if self.payload is None:
            return None
        return json_mapping(self.payload)


class _DistributedConfirmedSchedulePayload(ConfigPayload):
    confirmed: bool
    priority: int = 0
    max_attempts: int = 3


class _DistributedScheduleSettingsPayload(ConfigPayload):
    priority: int = 0
    max_attempts: int = 3


class _DistributedReasonPayload(ConfigPayload):
    reason: PydanticNonEmptyString


class _StateEventRepairPayload(ConfigPayload):
    confirmed: bool = False
    dry_run: bool = False


class _SessionResumePayload(ConfigPayload):
    confirmed: bool | None = None


class _MemoryCreatePayload(ConfigPayload):
    kind: str
    subject: str
    content: str
    scope: str = ""
    confidence: float = 1.0


class _SessionReasonPayload(ConfigPayload):
    reason: str


def _normalize_path(path: str) -> str:
    normalized = URL(path).path
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    normalized = normalized.rstrip("/")
    return normalized or "/"


def _optional_event_cursor(path: str) -> EventId | None:
    value = _optional_query_value(path, "after")
    if value is None:
        return None
    return EventId(value)


def _optional_session_cursor(path: str) -> SessionId | None:
    value = _optional_query_value(path, "after")
    if value is None:
        return None
    return SessionId(value)


def _optional_session_id_query(path: str) -> SessionId | None:
    value = _optional_query_value(path, "session_id")
    if value is None:
        return None
    return SessionId(value)


def _optional_bool_query(path: str, key: str) -> bool | None:
    value = _optional_query_value(path, key)
    if value is None:
        return None
    return parse_bool_text(value, key)


def _optional_float_query(
    path: str,
    key: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = _optional_query_value(path, key)
    if value is None:
        return default
    return parse_bounded_float_text(
        value,
        key,
        minimum=minimum,
        maximum=maximum,
    )


def _optional_positive_int_query(path: str, key: str) -> int | None:
    value = _optional_query_value(path, key)
    if value is None:
        return None
    return parse_positive_int_text(value, key)


def _optional_query_value(path: str, key: str) -> str | None:
    values = QueryParams(URL(path).query).getlist(key)
    if not values:
        return None
    if len(values) != 1:
        raise ValueError(f"{key} must be specified once")
    return parse_non_empty_string(values[0], key)


@cache
def _route(template: str) -> Route:
    return Route(template, _route_endpoint, methods=["GET"])


def _match_path(path: str, template: str) -> Mapping[str, str] | None:
    match, child_scope = _route(template).matches(
        {
            "type": "http",
            "path": _route_path(path),
            "root_path": "",
            "method": "GET",
        }
    )
    if match is not Match.FULL:
        return None
    params = child_scope.get("path_params", {})
    if not isinstance(params, Mapping):
        return None
    return {str(key): str(value) for key, value in params.items()}


def _route_path(path: str) -> str:
    parsed_path = URL(path).path
    if not parsed_path.startswith("/"):
        parsed_path = "/" + parsed_path
    return "/" + "/".join(segment for segment in parsed_path.split("/") if segment)


async def _route_endpoint() -> None:
    return None


def _distributed_lock_acquire_payload(body: JsonMapping) -> _DistributedLockAcquirePayload:
    return _request_model_payload(
        _DistributedLockAcquirePayload,
        body,
        {
            "lock_key": "distributed lock key must be a string",
            "owner_id": "distributed lock owner_id must be a string",
            "ttl_seconds": "distributed lock ttl_seconds must be a positive number",
            "metadata": "distributed lock metadata must be an object",
        },
        empty_field_messages={
            "lock_key": "distributed lock key must not be empty",
            "owner_id": "distributed lock owner_id must not be empty",
        },
    )


def _distributed_lock_lease_payload(body: JsonMapping) -> _DistributedLockLeasePayload:
    return _request_model_payload(
        _DistributedLockLeasePayload,
        body,
        {
            "owner_id": "distributed lock owner_id must be a string",
            "ttl_seconds": "distributed lock ttl_seconds must be a positive number",
        },
        empty_field_messages={"owner_id": "distributed lock owner_id must not be empty"},
    )


def _distributed_worker_registration_payload(
    body: JsonMapping,
) -> _DistributedWorkerRegistrationPayload:
    return _request_model_payload(
        _DistributedWorkerRegistrationPayload,
        body,
        {
            "capabilities": "distributed worker capabilities must be a list of strings",
            "ttl_seconds": "distributed worker ttl_seconds must be a positive number",
            "metadata": "distributed worker metadata must be an object",
        },
    )


def _distributed_worker_ttl_seconds(body: JsonMapping) -> float:
    payload = _request_model_payload(
        _DistributedWorkerTtlPayload,
        body,
        {"ttl_seconds": "distributed worker ttl_seconds must be a positive number"},
    )
    return payload.ttl_seconds


def _distributed_worker_run_payload(body: JsonMapping) -> _DistributedWorkerRunPayload:
    return _request_model_payload(
        _DistributedWorkerRunPayload,
        body,
        {
            "lease_ttl_seconds": "distributed worker lease_ttl_seconds must be a positive number",
            "worker_ttl_seconds": "distributed worker worker_ttl_seconds must be a positive number",
            "heartbeat_interval_seconds": (
                "distributed worker heartbeat_interval_seconds must be a positive number"
            ),
        },
    )


def _distributed_worker_run_batch_payload(body: JsonMapping) -> _DistributedWorkerRunBatchPayload:
    return _request_model_payload(
        _DistributedWorkerRunBatchPayload,
        body,
        {
            "max_items": "distributed worker max_items must be a positive integer",
            "lease_ttl_seconds": "distributed worker lease_ttl_seconds must be a positive number",
            "worker_ttl_seconds": "distributed worker worker_ttl_seconds must be a positive number",
            "heartbeat_interval_seconds": (
                "distributed worker heartbeat_interval_seconds must be a positive number"
            ),
        },
    )


def _distributed_schedule_payload(body: JsonMapping) -> _DistributedSchedulePayload:
    return _request_model_payload(
        _DistributedSchedulePayload,
        body,
        {
            "payload": "distributed schedule payload must be an object",
            "priority": "distributed schedule priority must be an integer",
            "max_attempts": "distributed schedule max_attempts must be an integer",
        },
    )


def _distributed_schedule_settings_payload(
    body: JsonMapping,
) -> _DistributedScheduleSettingsPayload:
    return _request_model_payload(
        _DistributedScheduleSettingsPayload,
        body,
        {
            "priority": "distributed schedule priority must be an integer",
            "max_attempts": "distributed schedule max_attempts must be an integer",
        },
    )


def _distributed_confirmed_schedule_payload(
    body: JsonMapping,
    *,
    confirmed_field_name: str,
) -> _DistributedConfirmedSchedulePayload:
    return _request_model_payload(
        _DistributedConfirmedSchedulePayload,
        body,
        {
            "confirmed": f"{confirmed_field_name} must be a boolean",
            "priority": "distributed schedule priority must be an integer",
            "max_attempts": "distributed schedule max_attempts must be an integer",
        },
    )


def _distributed_reason_payload(
    body: JsonMapping,
    *,
    default: str,
    field_name: str,
) -> str:
    payload = _request_model_payload(
        _DistributedReasonPayload,
        {**dict(body), "reason": body.get("reason", default)},
        {"reason": f"{field_name} must be a string"},
        empty_field_messages={"reason": f"{field_name} must not be empty"},
    )
    return payload.reason


def _state_event_repair_payload(body: JsonMapping) -> _StateEventRepairPayload:
    return _request_model_payload(
        _StateEventRepairPayload,
        body,
        {
            "confirmed": "state/event repair confirmed must be a boolean",
            "dry_run": "state/event repair dry_run must be a boolean",
        },
    )


def _memory_create_payload(body: JsonMapping) -> _MemoryCreatePayload:
    return _request_model_payload(
        _MemoryCreatePayload,
        body,
        {
            "kind": "memory kind must be a string",
            "subject": "memory subject must be a string",
            "content": "memory content must be a string",
            "scope": "memory scope must be a string",
            "confidence": "memory confidence must be a number",
        },
    )


def _session_resume_payload(body: JsonMapping) -> _SessionResumePayload:
    return _request_model_payload(
        _SessionResumePayload,
        body,
        {"confirmed": "resume confirmed must be a boolean"},
    )


def _session_reason_payload(
    body: JsonMapping,
    *,
    default: str,
    field_name: str,
) -> str:
    payload = _request_model_payload(
        _SessionReasonPayload,
        {**dict(body), "reason": body.get("reason", default)},
        {"reason": f"{field_name} must be a string"},
    )
    return payload.reason


def _request_model_payload[T: ConfigPayload](
    model_type: type[T],
    body: Mapping[str, object],
    field_messages: Mapping[str, str],
    *,
    empty_field_messages: Mapping[str, str] | None = None,
) -> T:
    try:
        return model_type.model_validate(dict(body))
    except PydanticValidationError as exc:
        extra_messages = dict(empty_field_messages) if empty_field_messages is not None else {}
        raise ValueError(
            _request_payload_error_message(
                exc,
                field_messages,
                empty_field_messages=extra_messages,
            )
        ) from exc


def _request_payload_error_message(
    error: PydanticValidationError,
    field_messages: Mapping[str, str],
    *,
    empty_field_messages: Mapping[str, str],
) -> str:
    details = pydantic_error_details(error)
    path = details.path
    if path:
        if path.startswith("capabilities["):
            return f"distributed worker {path} must be a non-empty string"
        if details.error_type == "value_error" and details.message.endswith("must not be empty"):
            message = empty_field_messages.get(path.partition("[")[0].partition(".")[0])
            if message is not None:
                return message
        message = field_messages.get(path.partition("[")[0].partition(".")[0])
        if message is not None:
            return message
    if details.message:
        return details.message.removeprefix("Value error, ")
    return str(error)


def _console_domain_view(
    snapshot: WebConsoleSnapshot,
    name: str,
    version: str | None,
) -> DomainView | None:
    matches = tuple(
        domain
        for domain in snapshot.domains
        if domain.name == name and (version is None or domain.version == version)
    )
    if len(matches) != 1:
        return None
    return matches[0]


def _console_domain_package_view(
    snapshot: WebConsoleSnapshot,
    name: str,
    version: str | None,
) -> DomainPackageView | None:
    matches = tuple(
        package
        for package in snapshot.domain_packages
        if package.name == name and (version is None or package.version == version)
    )
    if len(matches) != 1:
        return None
    return matches[0]


def _domain_not_found_message(name: str, version: str | None) -> str:
    if version is None:
        return f"domain not found or ambiguous: {name}"
    return f"domain not found: {name}@{version}"


def _domain_package_not_found_message(name: str, version: str | None) -> str:
    if version is None:
        return f"domain package not found or ambiguous: {name}"
    return f"domain package not found: {name}@{version}"
