from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache

from pydantic import Field, TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from starlette.datastructures import URL, QueryParams
from starlette.routing import Match, Route

from universal_agent.core import EventId, JsonMapping, SessionId
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    json_mapping,
    parse_non_empty_string,
    pydantic_error_details,
)
from universal_agent.distributed import DistributedLockOwnerId
from universal_agent.service import DomainPackageView, DomainView
from universal_agent.web import WebConsoleSnapshot


@dataclass(frozen=True, slots=True)
class _DistributedLockAcquirePayload:
    lock_key: str
    owner_id: DistributedLockOwnerId
    ttl_seconds: float
    metadata: JsonMapping | None = None


@dataclass(frozen=True, slots=True)
class _DistributedLockLeasePayload:
    owner_id: DistributedLockOwnerId
    ttl_seconds: float


@dataclass(frozen=True, slots=True)
class _DistributedWorkerRegistrationPayload:
    capabilities: tuple[str, ...]
    ttl_seconds: float
    metadata: JsonMapping | None = None


@dataclass(frozen=True, slots=True)
class _DistributedWorkerRunPayload:
    lease_ttl_seconds: float
    worker_ttl_seconds: float
    heartbeat_interval_seconds: float | None


@dataclass(frozen=True, slots=True)
class _DistributedWorkerRunBatchPayload:
    max_items: int
    lease_ttl_seconds: float
    worker_ttl_seconds: float
    heartbeat_interval_seconds: float | None


@dataclass(frozen=True, slots=True)
class _DistributedSchedulePayload:
    payload: JsonMapping | None
    priority: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class _DistributedConfirmedSchedulePayload:
    confirmed: bool
    priority: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class _DistributedScheduleSettingsPayload:
    priority: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class _StateEventRepairPayload:
    confirmed: bool
    dry_run: bool


@dataclass(frozen=True, slots=True)
class _SessionResumePayload:
    confirmed: bool | None = None


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


_BOOL_QUERY_ADAPTER: TypeAdapter[bool] = TypeAdapter(bool)
_FLOAT_QUERY_ADAPTER: TypeAdapter[float] = TypeAdapter(float)
_INT_QUERY_ADAPTER: TypeAdapter[int] = TypeAdapter(int)


class _DistributedLockAcquireModel(ConfigPayload):
    lock_key: str
    owner_id: str
    ttl_seconds: float = Field(default=30.0, gt=0)
    metadata: dict[str, PydanticJsonValue] | None = None


class _DistributedLockLeaseModel(ConfigPayload):
    owner_id: str
    ttl_seconds: float = Field(default=30.0, gt=0)


class _DistributedWorkerRegistrationModel(ConfigPayload):
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, PydanticJsonValue] | None = None
    ttl_seconds: float = Field(default=30.0, gt=0)


class _DistributedWorkerTtlModel(ConfigPayload):
    ttl_seconds: float = Field(default=30.0, gt=0)


class _DistributedWorkerRunModel(ConfigPayload):
    lease_ttl_seconds: float = Field(default=30.0, gt=0)
    worker_ttl_seconds: float = Field(default=30.0, gt=0)
    heartbeat_interval_seconds: float | None = Field(default=None, gt=0)


class _DistributedWorkerRunBatchModel(_DistributedWorkerRunModel):
    max_items: int = Field(default=1, ge=1)


class _DistributedScheduleModel(ConfigPayload):
    payload: dict[str, PydanticJsonValue] | None = None
    priority: int = 0
    max_attempts: int = 3


class _DistributedConfirmedScheduleModel(ConfigPayload):
    confirmed: bool
    priority: int = 0
    max_attempts: int = 3


class _DistributedScheduleSettingsModel(ConfigPayload):
    priority: int = 0
    max_attempts: int = 3


class _DistributedReasonModel(ConfigPayload):
    reason: str


class _StateEventRepairModel(ConfigPayload):
    confirmed: bool = False
    dry_run: bool = False


class _SessionResumeModel(ConfigPayload):
    confirmed: bool | None = None


class _SessionReasonModel(ConfigPayload):
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
    try:
        return _BOOL_QUERY_ADAPTER.validate_python(value)
    except PydanticValidationError as exc:
        raise ValueError(f"{key} must be a boolean") from exc


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
    try:
        parsed = _FLOAT_QUERY_ADAPTER.validate_python(value)
    except PydanticValidationError as exc:
        raise ValueError(f"{key} must be a number") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{key} must be between {minimum:g} and {maximum:g}")
    return parsed


def _optional_positive_int_query(path: str, key: str) -> int | None:
    value = _optional_query_value(path, key)
    if value is None:
        return None
    try:
        parsed = _INT_QUERY_ADAPTER.validate_python(value)
    except PydanticValidationError as exc:
        raise ValueError(f"{key} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{key} must be a positive integer")
    return parsed


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
    payload = _request_model_payload(
        _DistributedLockAcquireModel,
        body,
        {
            "lock_key": "distributed lock key must be a string",
            "owner_id": "distributed lock owner_id must be a string",
            "ttl_seconds": "distributed lock ttl_seconds must be a positive number",
            "metadata": "distributed lock metadata must be an object",
        },
    )
    return _DistributedLockAcquirePayload(
        lock_key=parse_non_empty_string(payload.lock_key, "distributed lock key"),
        owner_id=DistributedLockOwnerId(
            parse_non_empty_string(payload.owner_id, "distributed lock owner_id")
        ),
        ttl_seconds=payload.ttl_seconds,
        metadata=None if payload.metadata is None else json_mapping(payload.metadata),
    )


def _distributed_lock_lease_payload(body: JsonMapping) -> _DistributedLockLeasePayload:
    payload = _request_model_payload(
        _DistributedLockLeaseModel,
        body,
        {
            "owner_id": "distributed lock owner_id must be a string",
            "ttl_seconds": "distributed lock ttl_seconds must be a positive number",
        },
    )
    return _DistributedLockLeasePayload(
        owner_id=DistributedLockOwnerId(
            parse_non_empty_string(payload.owner_id, "distributed lock owner_id")
        ),
        ttl_seconds=payload.ttl_seconds,
    )


def _distributed_worker_registration_payload(
    body: JsonMapping,
) -> _DistributedWorkerRegistrationPayload:
    payload = _request_model_payload(
        _DistributedWorkerRegistrationModel,
        body,
        {
            "capabilities": "distributed worker capabilities must be a list of strings",
            "ttl_seconds": "distributed worker ttl_seconds must be a positive number",
            "metadata": "distributed worker metadata must be an object",
        },
    )
    capabilities: list[str] = []
    for index, item in enumerate(payload.capabilities):
        capabilities.append(
            parse_non_empty_string(
                item,
                f"distributed worker capabilities[{index}]",
                empty_template=(
                    f"distributed worker capabilities[{index}] must be a non-empty string"
                ),
            )
        )
    return _DistributedWorkerRegistrationPayload(
        capabilities=tuple(capabilities),
        ttl_seconds=payload.ttl_seconds,
        metadata=None if payload.metadata is None else json_mapping(payload.metadata),
    )


def _distributed_worker_ttl_seconds(body: JsonMapping) -> float:
    payload = _request_model_payload(
        _DistributedWorkerTtlModel,
        body,
        {"ttl_seconds": "distributed worker ttl_seconds must be a positive number"},
    )
    return payload.ttl_seconds


def _distributed_worker_run_payload(body: JsonMapping) -> _DistributedWorkerRunPayload:
    payload = _request_model_payload(
        _DistributedWorkerRunModel,
        body,
        {
            "lease_ttl_seconds": "distributed worker lease_ttl_seconds must be a positive number",
            "worker_ttl_seconds": "distributed worker worker_ttl_seconds must be a positive number",
            "heartbeat_interval_seconds": (
                "distributed worker heartbeat_interval_seconds must be a positive number"
            ),
        },
    )
    return _DistributedWorkerRunPayload(
        lease_ttl_seconds=payload.lease_ttl_seconds,
        worker_ttl_seconds=payload.worker_ttl_seconds,
        heartbeat_interval_seconds=payload.heartbeat_interval_seconds,
    )


def _distributed_worker_run_batch_payload(body: JsonMapping) -> _DistributedWorkerRunBatchPayload:
    payload = _request_model_payload(
        _DistributedWorkerRunBatchModel,
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
    return _DistributedWorkerRunBatchPayload(
        max_items=payload.max_items,
        lease_ttl_seconds=payload.lease_ttl_seconds,
        worker_ttl_seconds=payload.worker_ttl_seconds,
        heartbeat_interval_seconds=payload.heartbeat_interval_seconds,
    )


def _distributed_schedule_payload(body: JsonMapping) -> _DistributedSchedulePayload:
    payload = _request_model_payload(
        _DistributedScheduleModel,
        body,
        {
            "payload": "distributed schedule payload must be an object",
            "priority": "distributed schedule priority must be an integer",
            "max_attempts": "distributed schedule max_attempts must be an integer",
        },
    )
    return _DistributedSchedulePayload(
        payload=None if payload.payload is None else json_mapping(payload.payload),
        priority=payload.priority,
        max_attempts=payload.max_attempts,
    )


def _distributed_schedule_settings_payload(
    body: JsonMapping,
) -> _DistributedScheduleSettingsPayload:
    payload = _request_model_payload(
        _DistributedScheduleSettingsModel,
        body,
        {
            "priority": "distributed schedule priority must be an integer",
            "max_attempts": "distributed schedule max_attempts must be an integer",
        },
    )
    return _DistributedScheduleSettingsPayload(
        priority=payload.priority,
        max_attempts=payload.max_attempts,
    )


def _distributed_confirmed_schedule_payload(
    body: JsonMapping,
    *,
    confirmed_field_name: str,
) -> _DistributedConfirmedSchedulePayload:
    payload = _request_model_payload(
        _DistributedConfirmedScheduleModel,
        body,
        {
            "confirmed": f"{confirmed_field_name} must be a boolean",
            "priority": "distributed schedule priority must be an integer",
            "max_attempts": "distributed schedule max_attempts must be an integer",
        },
    )
    return _DistributedConfirmedSchedulePayload(
        confirmed=payload.confirmed,
        priority=payload.priority,
        max_attempts=payload.max_attempts,
    )


def _distributed_reason_payload(
    body: JsonMapping,
    *,
    default: str,
    field_name: str,
) -> str:
    payload = _request_model_payload(
        _DistributedReasonModel,
        {**dict(body), "reason": body.get("reason", default)},
        {"reason": f"{field_name} must be a string"},
    )
    return parse_non_empty_string(payload.reason, field_name)


def _state_event_repair_payload(body: JsonMapping) -> _StateEventRepairPayload:
    payload = _request_model_payload(
        _StateEventRepairModel,
        body,
        {
            "confirmed": "state/event repair confirmed must be a boolean",
            "dry_run": "state/event repair dry_run must be a boolean",
        },
    )
    return _StateEventRepairPayload(
        confirmed=payload.confirmed,
        dry_run=payload.dry_run,
    )


def _session_resume_payload(body: JsonMapping) -> _SessionResumePayload:
    payload = _request_model_payload(
        _SessionResumeModel,
        body,
        {"confirmed": "resume confirmed must be a boolean"},
    )
    return _SessionResumePayload(confirmed=payload.confirmed)


def _session_reason_payload(
    body: JsonMapping,
    *,
    default: str,
    field_name: str,
) -> str:
    payload = _request_model_payload(
        _SessionReasonModel,
        {**dict(body), "reason": body.get("reason", default)},
        {"reason": f"{field_name} must be a string"},
    )
    return payload.reason


def _request_model_payload[T: ConfigPayload](
    model_type: type[T],
    body: Mapping[str, object],
    field_messages: Mapping[str, str],
) -> T:
    try:
        return model_type.model_validate(dict(body))
    except PydanticValidationError as exc:
        raise ValueError(_request_payload_error_message(exc, field_messages)) from exc


def _request_payload_error_message(
    error: PydanticValidationError,
    field_messages: Mapping[str, str],
) -> str:
    details = pydantic_error_details(error)
    path = details.path
    if path:
        if path.startswith("capabilities["):
            return f"distributed worker {path} must be a non-empty string"
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
