from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError
from starlette.datastructures import URL, QueryParams
from starlette.routing import Match, Route

from universal_agent.core import ActionId, EventId, JsonMapping, SessionId, TaskId
from universal_agent.core.config_validation import ConfigPayload, PydanticJsonValue, json_mapping
from universal_agent.distributed import (
    DistributedLockLeaseId,
    DistributedLockOwnerId,
    WorkerId,
    WorkItemId,
)
from universal_agent.service import DomainPackageView, DomainView
from universal_agent.web import (
    WebCatalogPage,
    WebConsoleSnapshot,
    render_web_evidence_explorer,
    render_web_session_detail,
    render_web_world_model_explorer,
)


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
    normalized = value.lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"{key} must be a boolean")


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
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{key} must be between {minimum:g} and {maximum:g}")
    return parsed


def _optional_positive_int_query(path: str, key: str) -> int | None:
    value = _optional_query_value(path, key)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
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
    value = values[0]
    if not value.strip():
        raise ValueError(f"{key} must not be empty")
    return value


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


def _non_blank_path_param(params: Mapping[str, str], key: str) -> str | None:
    value = params.get(key)
    if value is None or not value.strip():
        return None
    return value


def _session_route(path: str) -> tuple[SessionId | None, str]:
    params = _match_path(path, "/v1/sessions/{session_id}")
    if params is not None:
        return SessionId(params["session_id"]), ""
    params = _match_path(path, "/v1/sessions/{session_id}/{suffix}")
    if params is not None:
        return SessionId(params["session_id"]), params["suffix"]
    params = _match_path(path, "/v1/sessions/{session_id}/{first_suffix}/{second_suffix}")
    if params is not None:
        suffix = f"{params['first_suffix']}/{params['second_suffix']}"
        return SessionId(params["session_id"]), suffix
    return None, ""


def _console_session_route(path: str) -> tuple[SessionId | None, str]:
    params = _match_path(path, "/console/sessions/{session_id}")
    if params is not None:
        session_id = _non_blank_path_param(params, "session_id")
        if session_id is not None:
            return SessionId(session_id), ""
    params = _match_path(path, "/console/sessions/{session_id}/{suffix}")
    if params is not None:
        session_id = _non_blank_path_param(params, "session_id")
        if session_id is not None:
            return SessionId(session_id), params["suffix"]
    return None, ""


def _console_catalog_route(path: str) -> WebCatalogPage | None:
    params = _match_path(path, "/console/{page}")
    if params is None:
        return None
    try:
        return WebCatalogPage(params["page"])
    except ValueError:
        return None


def _console_domain_route(path: str) -> tuple[str | None, str | None]:
    params = _match_path(path, "/console/domains/{name}")
    if params is not None:
        name = _non_blank_path_param(params, "name")
        if name is not None:
            return name, None
    params = _match_path(path, "/console/domains/{name}/{version}")
    if params is not None:
        name = _non_blank_path_param(params, "name")
        version = _non_blank_path_param(params, "version")
        if name is not None and version is not None:
            return name, version
    return None, None


def _console_domain_package_route(path: str) -> tuple[str | None, str | None]:
    params = _match_path(path, "/console/domain-packages/{name}")
    if params is not None:
        name = _non_blank_path_param(params, "name")
        if name is not None:
            return name, None
    params = _match_path(path, "/console/domain-packages/{name}/{version}")
    if params is not None:
        name = _non_blank_path_param(params, "name")
        version = _non_blank_path_param(params, "version")
        if name is not None and version is not None:
            return name, version
    return None, None


def _distributed_lock_lease_route(path: str) -> tuple[DistributedLockLeaseId | None, str]:
    params = _match_path(path, "/v1/distributed/lock-leases/{lease_id}/{action}")
    if params is not None:
        lease_id = _non_blank_path_param(params, "lease_id")
        action = _non_blank_path_param(params, "action")
        if lease_id is not None and action is not None:
            return DistributedLockLeaseId(lease_id), action
    return None, ""


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
        lock_key=_non_empty_string(payload.lock_key, "distributed lock key"),
        owner_id=DistributedLockOwnerId(
            _non_empty_string(payload.owner_id, "distributed lock owner_id")
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
            _non_empty_string(payload.owner_id, "distributed lock owner_id")
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
            _non_empty_string(
                item,
                f"distributed worker capabilities[{index}]",
                empty_message=(
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


def _distributed_worker_action_route(path: str) -> tuple[WorkerId | None, str]:
    params = _match_path(path, "/v1/distributed/workers/{worker_id}/{action}")
    if params is not None:
        worker_id = _non_blank_path_param(params, "worker_id")
        action = _non_blank_path_param(params, "action")
        if worker_id is not None and action is not None:
            return WorkerId(worker_id), action
    return None, ""


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
    return _non_empty_string(payload.reason, field_name)


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
    errors = error.errors(include_url=False)
    if not errors:
        return str(error)
    first = errors[0]
    location = first.get("loc", ())
    if isinstance(location, tuple) and location:
        field = str(location[0])
        if field == "capabilities" and len(location) > 1:
            return f"distributed worker capabilities[{location[1]}] must be a non-empty string"
        message = field_messages.get(field)
        if message is not None:
            return message
    message = str(first.get("msg", ""))
    if message:
        return message.removeprefix("Value error, ")
    return str(error)


def _non_empty_string(value: str, field_name: str, *, empty_message: str | None = None) -> str:
    if not value.strip():
        raise ValueError(empty_message or f"{field_name} must not be empty")
    return value


def _distributed_schedule_session_route(path: str) -> SessionId | None:
    params = _match_path(path, "/v1/distributed/sessions/{session_id}/schedule")
    if params is None:
        return None
    session_id = _non_blank_path_param(params, "session_id")
    if session_id is None:
        return None
    return SessionId(session_id)


def _distributed_schedule_task_route(path: str) -> tuple[SessionId | None, TaskId | None]:
    params = _match_path(
        path,
        "/v1/distributed/sessions/{session_id}/tasks/{task_id}/schedule",
    )
    if params is not None:
        session_id = _non_blank_path_param(params, "session_id")
        task_id = _non_blank_path_param(params, "task_id")
        if session_id is not None and task_id is not None:
            return SessionId(session_id), TaskId(task_id)
    return None, None


def _distributed_schedule_action_route(
    path: str,
) -> tuple[SessionId | None, TaskId | None, ActionId | None]:
    params = _match_path(
        path,
        "/v1/distributed/sessions/{session_id}/tasks/{task_id}/actions/{action_id}/schedule",
    )
    if params is not None:
        session_id = _non_blank_path_param(params, "session_id")
        task_id = _non_blank_path_param(params, "task_id")
        action_id = _non_blank_path_param(params, "action_id")
        if session_id is not None and task_id is not None and action_id is not None:
            return SessionId(session_id), TaskId(task_id), ActionId(action_id)
    return None, None, None


def _distributed_cancel_route(path: str) -> WorkItemId | None:
    params = _match_path(path, "/v1/distributed/work-items/{work_item_id}/cancel")
    if params is None:
        return None
    work_item_id = _non_blank_path_param(params, "work_item_id")
    if work_item_id is None:
        return None
    return WorkItemId(work_item_id)


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


def _console_explorer_renderer(
    path: str,
) -> Callable[[WebConsoleSnapshot], str] | None:
    if path == "/console/evidence":
        return render_web_evidence_explorer
    if path == "/console/world":
        return render_web_world_model_explorer
    return None


def _console_session_renderer(
    suffix: str,
) -> Callable[[WebConsoleSnapshot], str] | None:
    if suffix == "":
        return render_web_session_detail
    if suffix == "evidence":
        return render_web_evidence_explorer
    if suffix == "world":
        return render_web_world_model_explorer
    return None


def _profile_route(path: str) -> str | None:
    params = _match_path(path, "/v1/profiles/{profile}")
    if params is None:
        return None
    return _non_blank_path_param(params, "profile")


def _domain_package_route(path: str) -> tuple[str | None, str | None]:
    params = _match_path(path, "/v1/domain-packages/{name}")
    if params is not None:
        name = _non_blank_path_param(params, "name")
        if name is not None:
            return name, None
    params = _match_path(path, "/v1/domain-packages/{name}/{version}")
    if params is not None:
        name = _non_blank_path_param(params, "name")
        version = _non_blank_path_param(params, "version")
        if name is not None and version is not None:
            return name, version
    return None, None
