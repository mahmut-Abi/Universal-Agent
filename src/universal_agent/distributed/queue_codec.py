from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from universal_agent.core import ActionId, JsonMapping, SessionId, TaskId, immutable_json
from universal_agent.distributed.queue_models import (
    LeaseId,
    WorkerId,
    WorkerLease,
    WorkItem,
    WorkItemId,
    WorkItemStatus,
)


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
    return WorkItem(
        work_item_id=WorkItemId(_required_str(payload, "work_item_id")),
        kind=_required_str(payload, "kind"),
        payload=immutable_json(_optional_mapping(payload.get("payload"), "payload")),
        session_id=_optional_new_type(payload.get("session_id"), SessionId, "session_id"),
        task_id=_optional_new_type(payload.get("task_id"), TaskId, "task_id"),
        action_id=_optional_new_type(payload.get("action_id"), ActionId, "action_id"),
        priority=_required_int(payload, "priority"),
        max_attempts=_required_int(payload, "max_attempts"),
        attempts=_required_int(payload, "attempts"),
        status=WorkItemStatus(_required_str(payload, "status")),
        created_at=_required_datetime(payload, "created_at"),
        available_at=_required_datetime(payload, "available_at"),
        lease=_decode_optional_worker_lease(payload.get("lease")),
        idempotency_key=_optional_str(payload.get("idempotency_key"), "idempotency_key"),
        completed_at=_optional_datetime(payload.get("completed_at"), "completed_at"),
        failed_at=_optional_datetime(payload.get("failed_at"), "failed_at"),
        cancelled_at=_optional_datetime(payload.get("cancelled_at"), "cancelled_at"),
        last_error=_optional_str(payload.get("last_error"), "last_error"),
    )


def _encode_worker_lease(lease: WorkerLease) -> dict[str, object]:
    return {
        "lease_id": str(lease.lease_id),
        "worker_id": str(lease.worker_id),
        "leased_at": lease.leased_at.isoformat(),
        "lease_expires_at": lease.lease_expires_at.isoformat(),
        "heartbeat_at": lease.heartbeat_at.isoformat(),
    }


def _decode_optional_worker_lease(value: object) -> WorkerLease | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("lease must be an object")
    return WorkerLease(
        lease_id=LeaseId(_required_str(value, "lease_id")),
        worker_id=WorkerId(_required_str(value, "worker_id")),
        leased_at=_required_datetime(value, "leased_at"),
        lease_expires_at=_required_datetime(value, "lease_expires_at"),
        heartbeat_at=_required_datetime(value, "heartbeat_at"),
    )


def _required_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_str(value: object, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _required_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _required_datetime(payload: dict[str, object], key: str) -> datetime:
    return _parse_datetime(_required_str(payload, key), key)


def _optional_datetime(value: object, key: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return _parse_datetime(value, key)


def _parse_datetime(value: str, key: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an ISO datetime") from exc


def _optional_mapping(value: object, key: str) -> JsonMapping:
    if value is None:
        return immutable_json()
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return immutable_json(value)


def _optional_new_type[T](
    value: object,
    factory: Callable[[str], T],
    key: str,
) -> T | None:
    text = _optional_str(value, key)
    if text is None:
        return None
    return factory(text)
