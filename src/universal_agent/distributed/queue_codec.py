from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from pydantic import ValidationError as PydanticValidationError

from universal_agent.core import ActionId, SessionId, TaskId, immutable_json
from universal_agent.core.config_validation import (
    PydanticJsonValue,
    json_mapping,
    pydantic_error_details,
)
from universal_agent.distributed.queue_models import (
    FencingToken,
    LeaseId,
    WorkerId,
    WorkerLease,
    WorkItem,
    WorkItemId,
    WorkItemStatus,
)


class _QueueCodecPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _WorkQueueFilePayload(_QueueCodecPayload):
    version: StrictInt
    items: list[dict[str, object]] = Field(default_factory=list)


class _WorkerLeasePayload(_QueueCodecPayload):
    lease_id: StrictStr
    worker_id: StrictStr
    leased_at: datetime
    lease_expires_at: datetime
    heartbeat_at: datetime
    fencing_token: StrictInt = 0


class _WorkItemPayload(_QueueCodecPayload):
    work_item_id: StrictStr
    kind: StrictStr
    payload: dict[str, PydanticJsonValue] | None = None
    session_id: StrictStr | None = None
    task_id: StrictStr | None = None
    action_id: StrictStr | None = None
    priority: StrictInt
    max_attempts: StrictInt
    attempts: StrictInt
    status: StrictStr
    created_at: datetime
    available_at: datetime
    lease: _WorkerLeasePayload | None = None
    idempotency_key: StrictStr | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    cancelled_at: datetime | None = None
    last_error: StrictStr | None = None


def _encode_work_item(item: WorkItem) -> dict[str, object]:
    return {
        "work_item_id": str(item.work_item_id),
        "kind": item.kind,
        "payload": dict(item.payload),
        "session_id": None if item.session_id is None else str(item.session_id),
        "task_id": None if item.task_id is None else str(item.task_id),
        "action_id": None if item.action_id is None else str(item.action_id),
        "priority": item.priority,
        "max_attempts": item.max_attempts,
        "attempts": item.attempts,
        "status": item.status.value,
        "created_at": item.created_at.isoformat(),
        "available_at": item.available_at.isoformat(),
        "lease": None if item.lease is None else _encode_worker_lease(item.lease),
        "idempotency_key": item.idempotency_key,
        "completed_at": None if item.completed_at is None else item.completed_at.isoformat(),
        "failed_at": None if item.failed_at is None else item.failed_at.isoformat(),
        "cancelled_at": None if item.cancelled_at is None else item.cancelled_at.isoformat(),
        "last_error": item.last_error,
    }


def _decode_work_item(payload: dict[str, object]) -> WorkItem:
    item = _parse_queue_payload(_WorkItemPayload, payload)
    return WorkItem(
        work_item_id=WorkItemId(item.work_item_id),
        kind=item.kind,
        payload=immutable_json(json_mapping(item.payload or {})),
        session_id=None if item.session_id is None else SessionId(item.session_id),
        task_id=None if item.task_id is None else TaskId(item.task_id),
        action_id=None if item.action_id is None else ActionId(item.action_id),
        priority=item.priority,
        max_attempts=item.max_attempts,
        attempts=item.attempts,
        status=WorkItemStatus(item.status),
        created_at=item.created_at,
        available_at=item.available_at,
        lease=_decode_optional_worker_lease(item.lease),
        idempotency_key=item.idempotency_key,
        completed_at=item.completed_at,
        failed_at=item.failed_at,
        cancelled_at=item.cancelled_at,
        last_error=item.last_error,
    )


def _decode_work_queue_payload(payload: object) -> _WorkQueueFilePayload:
    try:
        return _WorkQueueFilePayload.model_validate(payload)
    except PydanticValidationError as exc:
        raise ValueError(_work_queue_file_payload_error_message(exc)) from exc


def _encode_worker_lease(lease: WorkerLease) -> dict[str, object]:
    return {
        "lease_id": str(lease.lease_id),
        "worker_id": str(lease.worker_id),
        "leased_at": lease.leased_at.isoformat(),
        "lease_expires_at": lease.lease_expires_at.isoformat(),
        "heartbeat_at": lease.heartbeat_at.isoformat(),
        "fencing_token": int(lease.fencing_token),
    }


def _decode_optional_worker_lease(value: _WorkerLeasePayload | None) -> WorkerLease | None:
    if value is None:
        return None
    return WorkerLease(
        lease_id=LeaseId(value.lease_id),
        worker_id=WorkerId(value.worker_id),
        leased_at=value.leased_at,
        lease_expires_at=value.lease_expires_at,
        heartbeat_at=value.heartbeat_at,
        fencing_token=FencingToken(value.fencing_token),
    )


def _parse_queue_payload[T: _QueueCodecPayload](
    payload_type: type[T],
    payload: object,
) -> T:
    try:
        return payload_type.model_validate(payload)
    except PydanticValidationError as exc:
        raise ValueError(_queue_payload_error_message(exc)) from exc


def _work_queue_file_payload_error_message(error: PydanticValidationError) -> str:
    details = pydantic_error_details(error)
    path = details.path
    error_type = details.error_type
    if not error_type:
        return details.message
    if not path and error_type in {"model_attributes_type", "model_type"}:
        return "file work queue payload must be an object"
    if path == "items" and error_type == "list_type":
        return "file work queue items must be a list"
    if path.startswith("items[") and error_type in {
        "dict_type",
        "model_attributes_type",
        "model_type",
    }:
        return f"file work queue {path} must be an object"
    if path == "version" and error_type in {"int_type", "missing"}:
        return "file work queue version must be an integer"
    return details.message or str(error)


def _queue_payload_error_message(error: PydanticValidationError) -> str:
    details = pydantic_error_details(error)
    path = details.path
    error_type = details.error_type
    if not error_type:
        return details.message
    if path == "lease" and error_type in {"dict_type", "model_attributes_type", "model_type"}:
        return "lease must be an object"
    if "datetime" in error_type:
        return f"{path} must be an ISO datetime"
    expected = _expected_error_type(error_type, path)
    if expected is not None:
        return f"{path} must be {expected}"
    if details.message:
        return details.message.removeprefix("Value error, ")
    return str(error)


def _expected_error_type(error_type: str, path: str) -> str | None:
    if error_type == "missing":
        return _missing_field_type(path)
    return {
        "dict_type": "an object",
        "int_type": "an integer",
        "invalid-json-value": "JSON-compatible",
        "model_attributes_type": "an object",
        "model_type": "an object",
        "string_type": "a string",
    }.get(error_type)


def _missing_field_type(path: str) -> str:
    if path in {
        "priority",
        "max_attempts",
        "attempts",
    }:
        return "an integer"
    if path in {
        "created_at",
        "available_at",
        "leased_at",
        "lease_expires_at",
        "heartbeat_at",
    }:
        return "an ISO datetime"
    if path == "payload":
        return "an object"
    return "a string"
