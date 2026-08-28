from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import NewType

from universal_agent.core import ActionId, JsonMapping, SessionId, TaskId, immutable_json, utc_now
from universal_agent.core.config_validation import (
    parse_non_empty_string,
    parse_non_negative_int,
    parse_positive_int,
)

WorkItemId = NewType("WorkItemId", str)
WorkerId = NewType("WorkerId", str)
LeaseId = NewType("LeaseId", str)


class WorkItemStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NoWorkAvailable(LookupError):
    pass


class WorkItemNotFoundError(LookupError):
    pass


class LeaseLostError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WorkerLease:
    lease_id: LeaseId
    worker_id: WorkerId
    leased_at: datetime
    lease_expires_at: datetime
    heartbeat_at: datetime


@dataclass(frozen=True, slots=True)
class WorkItem:
    work_item_id: WorkItemId
    kind: str
    payload: JsonMapping = field(default_factory=immutable_json)
    session_id: SessionId | None = None
    task_id: TaskId | None = None
    action_id: ActionId | None = None
    priority: int = 0
    max_attempts: int = 3
    attempts: int = 0
    status: WorkItemStatus = WorkItemStatus.QUEUED
    created_at: datetime = field(default_factory=utc_now)
    available_at: datetime = field(default_factory=utc_now)
    lease: WorkerLease | None = None
    idempotency_key: str | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    cancelled_at: datetime | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        parse_non_empty_string(str(self.work_item_id), "work_item_id")
        parse_non_empty_string(self.kind, "work item kind")
        parse_positive_int(self.max_attempts, "max_attempts")
        parse_non_negative_int(
            self.attempts,
            "attempts",
            range_template="{path} must be non-negative",
        )
        if self.attempts > self.max_attempts:
            raise ValueError("attempts must not exceed max_attempts")
        if self.status is WorkItemStatus.LEASED and self.lease is None:
            raise ValueError("leased work items require a lease")
        if self.status is not WorkItemStatus.LEASED and self.lease is not None:
            raise ValueError("only leased work items may carry a lease")
