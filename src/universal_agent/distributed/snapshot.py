from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from universal_agent.core import ActionId, JsonMapping, SessionId, TaskId
from universal_agent.distributed.locks import (
    DistributedLockLeaseId,
    DistributedLockOwnerId,
    InMemoryDistributedLockRegistry,
)
from universal_agent.distributed.queue import (
    InMemoryWorkQueue,
    WorkerId,
    WorkItem,
    WorkItemId,
    WorkItemStatus,
)
from universal_agent.distributed.worker_state import (
    InMemoryWorkerRegistry,
    WorkerRecord,
    WorkerStatus,
)


@dataclass(frozen=True, slots=True)
class WorkItemSnapshot:
    work_item_id: WorkItemId
    kind: str
    status: WorkItemStatus
    session_id: SessionId | None
    task_id: TaskId | None
    action_id: ActionId | None
    priority: int
    attempts: int
    max_attempts: int
    available_at: datetime
    worker_id: WorkerId | None
    lease_expires_at: datetime | None
    fencing_token: int | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class WorkQueueSnapshot:
    total_count: int
    queued_count: int
    leased_count: int
    completed_count: int
    failed_count: int
    cancelled_count: int
    items: tuple[WorkItemSnapshot, ...]


@dataclass(frozen=True, slots=True)
class DistributedLockSnapshot:
    lock_key: str
    owner_id: DistributedLockOwnerId
    lease_id: DistributedLockLeaseId
    acquired_at: datetime
    heartbeat_at: datetime
    lease_expires_at: datetime
    fencing_token: int
    metadata: JsonMapping


@dataclass(frozen=True, slots=True)
class WorkerSnapshot:
    worker_id: WorkerId
    status: WorkerStatus
    registered_at: datetime
    heartbeat_at: datetime
    lease_expires_at: datetime
    capabilities: tuple[str, ...]
    metadata: JsonMapping
    last_error: str | None


@dataclass(frozen=True, slots=True)
class WorkerRegistrySnapshot:
    total_count: int
    online_count: int
    draining_count: int
    offline_count: int
    lost_count: int
    workers: tuple[WorkerSnapshot, ...]


@dataclass(frozen=True, slots=True)
class DistributedRuntimeSnapshot:
    work_queue: WorkQueueSnapshot
    locks: tuple[DistributedLockSnapshot, ...]
    workers: WorkerRegistrySnapshot


def build_distributed_runtime_snapshot(
    *,
    queue: InMemoryWorkQueue,
    locks: InMemoryDistributedLockRegistry | None = None,
    workers: InMemoryWorkerRegistry | None = None,
) -> DistributedRuntimeSnapshot:
    """Build a read-only local P6 runtime coordination projection."""

    queue_items = tuple(_work_item_snapshot(item) for item in queue.list())
    worker_records: tuple[WorkerRecord, ...] = () if workers is None else workers.list()
    worker_items = tuple(_worker_snapshot(record) for record in worker_records)
    return DistributedRuntimeSnapshot(
        work_queue=WorkQueueSnapshot(
            total_count=len(queue_items),
            queued_count=_work_count(queue_items, WorkItemStatus.QUEUED),
            leased_count=_work_count(queue_items, WorkItemStatus.LEASED),
            completed_count=_work_count(queue_items, WorkItemStatus.COMPLETED),
            failed_count=_work_count(queue_items, WorkItemStatus.FAILED),
            cancelled_count=_work_count(queue_items, WorkItemStatus.CANCELLED),
            items=queue_items,
        ),
        locks=tuple(
            DistributedLockSnapshot(
                lock_key=lease.lock_key,
                owner_id=lease.owner_id,
                lease_id=lease.lease_id,
                acquired_at=lease.acquired_at,
                heartbeat_at=lease.heartbeat_at,
                lease_expires_at=lease.lease_expires_at,
                fencing_token=int(lease.fencing_token),
                metadata=lease.metadata,
            )
            for lease in (() if locks is None else locks.active())
        ),
        workers=WorkerRegistrySnapshot(
            total_count=len(worker_items),
            online_count=_worker_count(worker_items, WorkerStatus.ONLINE),
            draining_count=_worker_count(worker_items, WorkerStatus.DRAINING),
            offline_count=_worker_count(worker_items, WorkerStatus.OFFLINE),
            lost_count=_worker_count(worker_items, WorkerStatus.LOST),
            workers=worker_items,
        ),
    )


def _work_item_snapshot(item: WorkItem) -> WorkItemSnapshot:
    lease = item.lease
    return WorkItemSnapshot(
        work_item_id=item.work_item_id,
        kind=item.kind,
        status=item.status,
        session_id=item.session_id,
        task_id=item.task_id,
        action_id=item.action_id,
        priority=item.priority,
        attempts=item.attempts,
        max_attempts=item.max_attempts,
        available_at=item.available_at,
        worker_id=None if lease is None else lease.worker_id,
        lease_expires_at=None if lease is None else lease.lease_expires_at,
        fencing_token=None if lease is None else int(lease.fencing_token),
        last_error=item.last_error,
    )


def _worker_snapshot(record: WorkerRecord) -> WorkerSnapshot:
    return WorkerSnapshot(
        worker_id=record.worker_id,
        status=record.status,
        registered_at=record.registered_at,
        heartbeat_at=record.heartbeat_at,
        lease_expires_at=record.lease_expires_at,
        capabilities=record.capabilities,
        metadata=record.metadata,
        last_error=record.last_error,
    )


def _work_count(items: tuple[WorkItemSnapshot, ...], status: WorkItemStatus) -> int:
    return sum(1 for item in items if item.status is status)


def _worker_count(items: tuple[WorkerSnapshot, ...], status: WorkerStatus) -> int:
    return sum(1 for item in items if item.status is status)
