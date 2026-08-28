from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError

from universal_agent.core import (
    ActionId,
    ExecutionStatus,
    Goal,
    GoalId,
    GoalStatus,
    JsonMapping,
    JsonValue,
    SessionId,
    SuccessCriterion,
    Task,
    TaskId,
    immutable_json,
    parse_iso_datetime,
    utc_now,
)
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    PydanticNonEmptyString,
    pydantic_error_details,
)
from universal_agent.distributed import (
    DistributedCancellationResult,
    DistributedHealthReport,
    DistributedLockConflictError,
    DistributedLockLeaseId,
    DistributedLockLeaseLostError,
    DistributedLockLifecycleResult,
    DistributedLockOwnerId,
    DistributedMaintenanceResult,
    DistributedPruneResult,
    DistributedRuntimeCoordinator,
    DistributedRuntimeSnapshot,
    DistributedSchedulingResult,
    DistributedWorkerLifecycleResult,
    WorkerId,
    WorkerRunResult,
    WorkHandler,
    WorkHandlerResult,
    WorkItem,
    WorkItemId,
    WorkKind,
    WorkQueueWorker,
)
from universal_agent.runtime import RuntimeAPI, SessionSummaryView, SessionView
from universal_agent.service.projections import copy_json_value
from universal_agent.service.views import DistributedPendingActionSchedulingResult
from universal_agent.state import StateNotFoundError

if TYPE_CHECKING:
    from universal_agent.host.config import RuntimeConfig


_DISTRIBUTED_SESSION_LOCK_TTL_SECONDS = 300.0


class _GoalWorkSuccessCriterionPayload(ConfigPayload):
    key: PydanticNonEmptyString
    expected: PydanticJsonValue


class _GoalWorkPayload(ConfigPayload):
    id: PydanticNonEmptyString
    description: PydanticNonEmptyString
    success_criteria: list[_GoalWorkSuccessCriterionPayload] = Field(min_length=1)
    created_at: str | None = None


class _TaskWorkPayload(ConfigPayload):
    id: PydanticNonEmptyString
    description: PydanticNonEmptyString
    required_criteria: list[PydanticNonEmptyString]
    created_at: str | None = None


class _GoalTaskWorkPayload(ConfigPayload):
    goal: _GoalWorkPayload
    task: _TaskWorkPayload


class DistributedRuntimeController:
    """Distributed scheduling and worker execution behind RuntimeService.

    RuntimeService remains the product-facing interface. This controller keeps
    queue, worker, and session-lock mechanics localized so the service module
    does not become the implementation home for distributed execution.
    """

    def __init__(
        self,
        *,
        runtime_api: RuntimeAPI,
        coordinator: DistributedRuntimeCoordinator | None,
        config: RuntimeConfig | None = None,
    ) -> None:
        self._runtime_api = runtime_api
        self._coordinator = coordinator
        self._config = config

    def snapshot(self) -> DistributedRuntimeSnapshot | None:
        if self._coordinator is None:
            return None
        return self._coordinator.snapshot()

    def health(self, *, now: datetime | None = None) -> DistributedHealthReport | None:
        if self._coordinator is None:
            return None
        return self._coordinator.health(now=now)

    def schedule_session(
        self,
        session_id: SessionId,
        *,
        payload: JsonMapping | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> DistributedSchedulingResult | None:
        if self._coordinator is None:
            return None
        return self._coordinator.schedule_session(
            session_id,
            payload=payload,
            priority=priority,
            max_attempts=max_attempts,
            available_at=now,
            now=now,
        )

    def schedule_goal(
        self,
        goal: Goal,
        task: Task,
        *,
        priority: int = 0,
        max_attempts: int = 3,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> DistributedSchedulingResult | None:
        if self._coordinator is None:
            return None
        return self._coordinator.schedule_goal(
            payload=goal_work_payload(goal, task),
            idempotency_key=idempotency_key or goal_work_idempotency_key(goal, task),
            priority=priority,
            max_attempts=max_attempts,
            available_at=now,
            now=now,
        )

    def schedule_task(
        self,
        session_id: SessionId,
        task_id: TaskId,
        *,
        payload: JsonMapping | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> DistributedSchedulingResult | None:
        if self._coordinator is None:
            return None
        return self._coordinator.schedule_task(
            session_id,
            task_id,
            payload=payload,
            priority=priority,
            max_attempts=max_attempts,
            available_at=now,
            now=now,
        )

    def schedule_action(
        self,
        session_id: SessionId,
        task_id: TaskId,
        action_id: ActionId,
        *,
        confirmed: bool,
        priority: int = 0,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> DistributedSchedulingResult | None:
        if not confirmed:
            raise ValueError("distributed schedule-action requires confirmed=true")
        if self._coordinator is None:
            return None
        return self._coordinator.schedule_action(
            session_id,
            task_id,
            action_id,
            payload=immutable_json({"confirmed": confirmed}),
            priority=priority,
            max_attempts=max_attempts,
            available_at=now,
            now=now,
        )

    async def schedule_pending_actions(
        self,
        *,
        confirmed: bool,
        priority: int = 0,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> DistributedPendingActionSchedulingResult | None:
        if not confirmed:
            raise ValueError("distributed pending-action schedule requires confirmed=true")
        if self._coordinator is None:
            return None

        scheduled: list[WorkItem] = []
        for summary in await self._runtime_api.list_sessions():
            if summary.goal_status is not GoalStatus.WAITING or not summary.pending_action:
                continue
            session = await self._runtime_api.get_session(summary.session_id)
            pending = session.pending_action
            if pending is None or session.goal_status is not GoalStatus.WAITING:
                continue
            result = self._coordinator.schedule_action(
                session.session_id,
                session.current_task_id,
                pending.action_id,
                payload=immutable_json({"confirmed": confirmed}),
                priority=priority,
                max_attempts=max_attempts,
                available_at=now,
                now=now,
            )
            scheduled.append(result.scheduled_work_item)

        return DistributedPendingActionSchedulingResult(
            scheduled_work_items=tuple(scheduled),
            snapshot=self._coordinator.snapshot(),
            health=self._coordinator.health(now=now),
        )

    def register_worker(
        self,
        worker_id: WorkerId,
        *,
        capabilities: tuple[str, ...] = (),
        metadata: JsonMapping | None = None,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> DistributedWorkerLifecycleResult | None:
        if self._coordinator is None:
            return None
        return self._coordinator.register_worker(
            worker_id,
            capabilities=capabilities,
            metadata=metadata,
            ttl_seconds=ttl_seconds,
            now=now,
        )

    def heartbeat_worker(
        self,
        worker_id: WorkerId,
        *,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> DistributedWorkerLifecycleResult | None:
        if self._coordinator is None:
            return None
        return self._coordinator.heartbeat_worker(
            worker_id,
            ttl_seconds=ttl_seconds,
            now=now,
        )

    def drain_worker(
        self,
        worker_id: WorkerId,
        *,
        reason: str = "worker draining",
        now: datetime | None = None,
    ) -> DistributedWorkerLifecycleResult | None:
        if self._coordinator is None:
            return None
        return self._coordinator.drain_worker(
            worker_id,
            reason=reason,
            now=now,
        )

    def mark_worker_offline(
        self,
        worker_id: WorkerId,
        *,
        reason: str = "worker offline",
        now: datetime | None = None,
    ) -> DistributedWorkerLifecycleResult | None:
        if self._coordinator is None:
            return None
        return self._coordinator.mark_worker_offline(
            worker_id,
            reason=reason,
            now=now,
        )

    def acquire_lock(
        self,
        *,
        lock_key: str,
        owner_id: DistributedLockOwnerId,
        ttl_seconds: float = 30.0,
        metadata: JsonMapping | None = None,
        now: datetime | None = None,
    ) -> DistributedLockLifecycleResult | None:
        if self._coordinator is None:
            return None
        return self._coordinator.acquire_lock(
            lock_key=lock_key,
            owner_id=owner_id,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
            now=now,
        )

    def heartbeat_lock(
        self,
        lease_id: DistributedLockLeaseId,
        *,
        owner_id: DistributedLockOwnerId,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> DistributedLockLifecycleResult | None:
        if self._coordinator is None:
            return None
        return self._coordinator.heartbeat_lock(
            lease_id,
            owner_id=owner_id,
            ttl_seconds=ttl_seconds,
            now=now,
        )

    def release_lock(
        self,
        lease_id: DistributedLockLeaseId,
        *,
        owner_id: DistributedLockOwnerId,
        now: datetime | None = None,
    ) -> DistributedLockLifecycleResult | None:
        if self._coordinator is None:
            return None
        return self._coordinator.release_lock(
            lease_id,
            owner_id=owner_id,
            now=now,
        )

    def expire(self, *, now: datetime | None = None) -> DistributedMaintenanceResult | None:
        if self._coordinator is None:
            return None
        return self._coordinator.expire(now=now)

    def prune_terminal_work_items(
        self,
        *,
        before: datetime | None = None,
        now: datetime | None = None,
    ) -> DistributedPruneResult | None:
        if self._coordinator is None:
            return None
        timestamp = now or utc_now()
        retention_seconds = (
            None if self._config is None else self._config.distributed_terminal_retention_seconds
        )
        retention_before = (
            timestamp - timedelta(seconds=retention_seconds)
            if before is None and retention_seconds is not None
            else before
        )
        return self._coordinator.prune_terminal_work_items(
            before=retention_before,
            now=timestamp,
        )

    def cancel_work_item(
        self,
        work_item_id: WorkItemId,
        *,
        reason: str = "distributed work item cancelled",
        now: datetime | None = None,
    ) -> DistributedCancellationResult | None:
        if self._coordinator is None:
            return None
        return self._coordinator.cancel_work_item(
            work_item_id,
            reason=reason,
            now=now,
        )

    async def run_worker_once(
        self,
        worker_id: WorkerId,
        *,
        lease_ttl_seconds: float = 30.0,
        worker_ttl_seconds: float = 30.0,
        heartbeat_interval_seconds: float | None = None,
    ) -> WorkerRunResult | None:
        if self._coordinator is None:
            return None
        worker = WorkQueueWorker(
            queue=self._coordinator.queue,
            worker_id=worker_id,
            handlers=self._work_handlers(),
            lease_ttl_seconds=lease_ttl_seconds,
            worker_registry=self._coordinator.workers,
            worker_ttl_seconds=worker_ttl_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )
        return await worker.run_once()

    async def run_worker_until_idle(
        self,
        worker_id: WorkerId,
        *,
        max_items: int,
        lease_ttl_seconds: float = 30.0,
        worker_ttl_seconds: float = 30.0,
        heartbeat_interval_seconds: float | None = None,
    ) -> tuple[WorkerRunResult, ...] | None:
        if self._coordinator is None:
            return None
        worker = WorkQueueWorker(
            queue=self._coordinator.queue,
            worker_id=worker_id,
            handlers=self._work_handlers(),
            lease_ttl_seconds=lease_ttl_seconds,
            worker_registry=self._coordinator.workers,
            worker_ttl_seconds=worker_ttl_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )
        return await worker.run_until_idle(max_items=max_items)

    async def invalid_session_work_item_count(
        self,
        sessions: tuple[SessionSummaryView, ...],
        snapshot: DistributedRuntimeSnapshot,
    ) -> int:
        current_task_by_session = {
            session.session_id: session.current_task_id for session in sessions
        }
        full_session_by_id: dict[SessionId, SessionView] = {}
        invalid_count = 0
        for item in snapshot.work_queue.items:
            if item.kind == WorkKind.AGENT_SESSION.value:
                if item.session_id is None or item.session_id not in current_task_by_session:
                    invalid_count += 1
                continue
            if item.kind not in {WorkKind.TASK.value, WorkKind.TOOL_ACTION.value}:
                continue
            if item.session_id is None:
                invalid_count += 1
                continue
            expected_task_id = current_task_by_session.get(item.session_id)
            if expected_task_id is None or item.task_id is None or item.task_id != expected_task_id:
                invalid_count += 1
                continue
            if item.kind == WorkKind.TOOL_ACTION.value:
                if item.action_id is None:
                    invalid_count += 1
                    continue
                session = full_session_by_id.get(item.session_id)
                if session is None:
                    try:
                        session = await self._runtime_api.get_session(item.session_id)
                    except StateNotFoundError:
                        invalid_count += 1
                        continue
                    full_session_by_id[item.session_id] = session
                pending = session.pending_action
                if pending is None or pending.action_id != item.action_id:
                    invalid_count += 1
        return invalid_count

    def terminal_work_item_count(self, snapshot: DistributedRuntimeSnapshot) -> int:
        queue = snapshot.work_queue
        return queue.completed_count + queue.failed_count + queue.cancelled_count

    def _work_handlers(self) -> Mapping[str, WorkHandler]:
        return {
            WorkKind.AGENT_SESSION.value: self._handle_session_work,
            WorkKind.AGENT_GOAL.value: self._handle_goal_work,
            WorkKind.TASK.value: self._handle_task_work,
            WorkKind.TOOL_ACTION.value: self._handle_action_work,
        }

    async def _handle_session_work(self, item: WorkItem) -> WorkHandlerResult:
        if item.session_id is None:
            return WorkHandlerResult.failed(
                "agent_session work item missing session_id", retry=False
            )
        try:
            session = await self._runtime_api.get_session(item.session_id)
        except StateNotFoundError as exc:
            return WorkHandlerResult.failed(f"session not found: {exc}", retry=False)
        if session.pending_action is not None:
            return WorkHandlerResult.failed(
                "session requires explicit confirmation before distributed resume",
                retry=False,
            )
        if session.goal_status in {
            GoalStatus.COMPLETED,
            GoalStatus.FAILED,
            GoalStatus.CANCELLED,
        }:
            return WorkHandlerResult.completed(
                f"session already terminal: {session.goal_status.value}"
            )
        if session.goal_status is not GoalStatus.WAITING:
            return WorkHandlerResult.failed(
                f"session is not resumable: {session.goal_status.value}",
                retry=True,
            )
        return await self._resume_session_under_lock(
            item,
            confirmed=None,
            completed_reason_prefix="distributed session resume settled as",
            failure_reason_prefix="distributed session resume failed",
        )

    async def _handle_goal_work(self, item: WorkItem) -> WorkHandlerResult:
        try:
            goal, task = goal_task_from_work_payload(item.payload)
        except ValueError as exc:
            return WorkHandlerResult.failed(f"invalid agent_goal work payload: {exc}", retry=False)
        run = await self._runtime_api.run_goal(goal, task)
        if run.result.status is ExecutionStatus.COMPLETED:
            return WorkHandlerResult.completed(f"session completed: {run.result.session_id}")
        if run.result.status is ExecutionStatus.WAITING:
            return WorkHandlerResult.completed(f"session waiting: {run.result.session_id}")
        if run.result.status is ExecutionStatus.CANCELLED:
            return WorkHandlerResult.completed(f"session cancelled: {run.result.session_id}")
        return WorkHandlerResult.failed(
            f"distributed goal run failed: {run.result.reason}",
            retry=False,
        )

    async def _handle_task_work(self, item: WorkItem) -> WorkHandlerResult:
        if item.session_id is None:
            return WorkHandlerResult.failed("task work item missing session_id", retry=False)
        if item.task_id is None:
            return WorkHandlerResult.failed("task work item missing task_id", retry=False)
        try:
            session = await self._runtime_api.get_session(item.session_id)
        except StateNotFoundError as exc:
            return WorkHandlerResult.failed(f"session not found: {exc}", retry=False)
        if session.current_task_id != item.task_id:
            return WorkHandlerResult.failed(
                "task work item does not match current session task: "
                f"{item.task_id} != {session.current_task_id}",
                retry=False,
            )
        if session.pending_action is not None:
            return WorkHandlerResult.failed(
                "task requires explicit confirmation before distributed resume",
                retry=False,
            )
        if session.goal_status in {
            GoalStatus.COMPLETED,
            GoalStatus.FAILED,
            GoalStatus.CANCELLED,
        }:
            return WorkHandlerResult.completed(
                f"session already terminal: {session.goal_status.value}"
            )
        if session.goal_status is not GoalStatus.WAITING:
            return WorkHandlerResult.failed(
                f"session task is not resumable: {session.goal_status.value}",
                retry=True,
            )
        return await self._resume_session_under_lock(
            item,
            confirmed=None,
            completed_reason_prefix="distributed task resume settled as",
            failure_reason_prefix="distributed task resume failed",
        )

    async def _handle_action_work(self, item: WorkItem) -> WorkHandlerResult:
        if item.session_id is None:
            return WorkHandlerResult.failed("tool_action work item missing session_id", retry=False)
        if item.task_id is None:
            return WorkHandlerResult.failed("tool_action work item missing task_id", retry=False)
        if item.action_id is None:
            return WorkHandlerResult.failed("tool_action work item missing action_id", retry=False)
        if item.payload.get("confirmed") is not True:
            return WorkHandlerResult.failed(
                "tool_action work item requires confirmed=true",
                retry=False,
            )
        try:
            session = await self._runtime_api.get_session(item.session_id)
        except StateNotFoundError as exc:
            return WorkHandlerResult.failed(f"session not found: {exc}", retry=False)
        pending = session.pending_action
        if pending is None:
            return WorkHandlerResult.failed(
                "tool_action work item requires a pending action",
                retry=False,
            )
        if session.current_task_id != item.task_id:
            return WorkHandlerResult.failed(
                "tool_action work item does not match current session task: "
                f"{item.task_id} != {session.current_task_id}",
                retry=False,
            )
        if pending.action_id != item.action_id:
            return WorkHandlerResult.failed(
                "tool_action work item does not match pending action: "
                f"{item.action_id} != {pending.action_id}",
                retry=False,
            )
        if session.goal_status is not GoalStatus.WAITING:
            return WorkHandlerResult.failed(
                f"session action is not confirmable: {session.goal_status.value}",
                retry=True,
            )
        return await self._resume_session_under_lock(
            item,
            confirmed=True,
            completed_reason_prefix="distributed action resume settled as",
            failure_reason_prefix="distributed action resume failed",
        )

    async def _resume_session_under_lock(
        self,
        item: WorkItem,
        *,
        confirmed: bool | None,
        completed_reason_prefix: str,
        failure_reason_prefix: str,
    ) -> WorkHandlerResult:
        if item.session_id is None:
            return WorkHandlerResult.failed(
                "distributed session work item missing session_id",
                retry=False,
            )
        session_id = item.session_id

        async def resume() -> WorkHandlerResult:
            run = await self._runtime_api.resume_session(session_id, confirmed=confirmed)
            if run.result.status in {
                ExecutionStatus.COMPLETED,
                ExecutionStatus.WAITING,
                ExecutionStatus.CANCELLED,
            }:
                return WorkHandlerResult.completed(
                    f"{completed_reason_prefix} {run.result.status.value}"
                )
            return WorkHandlerResult.failed(
                f"{failure_reason_prefix}: {run.result.reason}",
                retry=False,
            )

        return await self._with_session_lock(item, resume)

    async def _with_session_lock(
        self,
        item: WorkItem,
        operation: Callable[[], Awaitable[WorkHandlerResult]],
    ) -> WorkHandlerResult:
        if self._coordinator is None:
            return await operation()
        if item.session_id is None:
            return WorkHandlerResult.failed(
                "distributed session lock requires session_id",
                retry=False,
            )
        if item.lease is None:
            return WorkHandlerResult.failed(
                "distributed session lock requires queue lease metadata",
                retry=False,
            )

        owner_id = distributed_session_lock_owner(item)
        lock_key = distributed_session_lock_key(item.session_id)
        try:
            lock = self._coordinator.locks.acquire(
                lock_key=lock_key,
                owner_id=owner_id,
                ttl_seconds=_DISTRIBUTED_SESSION_LOCK_TTL_SECONDS,
                metadata=immutable_json(
                    {
                        "work_item_id": str(item.work_item_id),
                        "work_kind": item.kind,
                        "queue_lease_id": str(item.lease.lease_id),
                    }
                ),
            )
        except DistributedLockConflictError as exc:
            return WorkHandlerResult.failed(
                f"session execution lock conflict: {exc}",
                retry=True,
            )

        try:
            return await operation()
        finally:
            with suppress(DistributedLockLeaseLostError):
                self._coordinator.locks.release(
                    lock.lease_id,
                    owner_id=owner_id,
                )


def distributed_session_lock_key(session_id: SessionId) -> str:
    return f"session/{session_id}"


def distributed_session_lock_owner(item: WorkItem) -> DistributedLockOwnerId:
    lease = item.lease
    if lease is None:
        return DistributedLockOwnerId(f"worker:unknown:work:{item.work_item_id}")
    return DistributedLockOwnerId(
        f"worker:{lease.worker_id}:work:{item.work_item_id}:lease:{lease.lease_id}"
    )


def goal_work_payload(goal: Goal, task: Task) -> JsonMapping:
    criteria: list[JsonValue] = []
    for criterion in goal.success_criteria:
        criteria.append(
            {
                "key": criterion.key,
                "expected": copy_json_value(criterion.expected),
            }
        )
    payload: dict[str, JsonValue] = {
        "goal": {
            "id": str(goal.id),
            "description": goal.description,
            "success_criteria": criteria,
            "created_at": goal.created_at.isoformat(),
        },
        "task": {
            "id": str(task.id),
            "description": task.description,
            "required_criteria": list(task.required_criteria),
            "created_at": task.created_at.isoformat(),
        },
    }
    return immutable_json(payload)


def goal_work_idempotency_key(goal: Goal, task: Task) -> str:
    return f"goal:{goal.id}:{task.id}"


def goal_task_from_work_payload(payload: Mapping[str, JsonValue]) -> tuple[Goal, Task]:
    work_payload = _parse_goal_task_work_payload(payload)
    goal_payload = work_payload.goal
    task_payload = work_payload.task
    return (
        Goal(
            goal_payload.description,
            _success_criteria_from_payload(goal_payload.success_criteria),
            id=GoalId(goal_payload.id),
            created_at=_datetime_payload_field(goal_payload.created_at, "goal.created_at"),
        ),
        Task(
            task_payload.description,
            tuple(task_payload.required_criteria),
            id=TaskId(task_payload.id),
            created_at=_datetime_payload_field(task_payload.created_at, "task.created_at"),
        ),
    )


def _parse_goal_task_work_payload(
    payload: Mapping[str, JsonValue],
) -> _GoalTaskWorkPayload:
    try:
        return _GoalTaskWorkPayload.model_validate(dict(payload))
    except PydanticValidationError as exc:
        raise ValueError(_goal_task_work_payload_error_message(exc)) from exc


def _success_criteria_from_payload(
    items: list[_GoalWorkSuccessCriterionPayload],
) -> tuple[SuccessCriterion, ...]:
    return tuple(SuccessCriterion(item.key, copy_json_value(item.expected)) for item in items)


def _datetime_payload_field(
    value: str | None,
    field: str,
) -> datetime:
    if value is None:
        return utc_now()
    return parse_iso_datetime(value, field=field)


def _goal_task_work_payload_error_message(error: PydanticValidationError) -> str:
    details = pydantic_error_details(error)
    path = details.path
    error_type = details.error_type
    if not error_type:
        return details.message
    if path == "goal.success_criteria" and error_type == "too_short":
        return "goal.success_criteria must not be empty"
    if error_type == "value_error" and details.message.endswith("must not be empty"):
        return f"{path} must not be empty"
    if path.endswith(".expected") and error_type == "missing":
        return f"{path} is required"
    expected = _goal_task_work_payload_expected_type(error_type, path)
    if expected is not None:
        return f"{path} must be {expected}"
    if details.message:
        return details.message.removeprefix("Value error, ")
    return str(error)


def _goal_task_work_payload_expected_type(error_type: str, path: str) -> str | None:
    if error_type == "missing":
        return _goal_task_work_payload_missing_field_type(path)
    return {
        "dict_type": "an object",
        "invalid-json-value": "JSON-compatible",
        "list_type": "a list",
        "model_attributes_type": "an object",
        "model_type": "an object",
        "string_type": "a string",
    }.get(error_type)


def _goal_task_work_payload_missing_field_type(path: str) -> str:
    if path.endswith(".success_criteria") or path.endswith(".required_criteria"):
        return "a list"
    if path in {"goal", "task"}:
        return "an object"
    return "a string"
