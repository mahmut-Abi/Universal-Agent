from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from universal_agent.core import (
    ActionId,
    JsonMapping,
    SessionId,
    TaskId,
    immutable_json,
)
from universal_agent.core.config_validation import parse_non_empty_string
from universal_agent.distributed.queue import InMemoryWorkQueue, WorkItem, WorkItemStatus


class WorkKind(StrEnum):
    AGENT_SESSION = "agent_session"
    AGENT_GOAL = "agent_goal"
    TASK = "task"
    TOOL_ACTION = "tool_action"


class WorkScheduler:
    """Local P6 scheduler facade that preserves runtime identity in queued work."""

    def __init__(self, queue: InMemoryWorkQueue) -> None:
        self._queue = queue

    def schedule_session(
        self,
        session_id: SessionId,
        *,
        payload: JsonMapping | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        available_at: datetime | None = None,
    ) -> WorkItem:
        _require_id(session_id, "session_id")
        return self._queue.enqueue(
            kind=WorkKind.AGENT_SESSION.value,
            payload=immutable_json(payload),
            session_id=session_id,
            priority=priority,
            max_attempts=max_attempts,
            available_at=available_at,
            idempotency_key=_session_key(session_id),
        )

    def schedule_goal(
        self,
        *,
        payload: JsonMapping | None = None,
        idempotency_key: str,
        priority: int = 0,
        max_attempts: int = 3,
        available_at: datetime | None = None,
    ) -> WorkItem:
        parse_non_empty_string(idempotency_key, "idempotency_key")
        return self._queue.enqueue(
            kind=WorkKind.AGENT_GOAL.value,
            payload=immutable_json(payload),
            priority=priority,
            max_attempts=max_attempts,
            available_at=available_at,
            idempotency_key=idempotency_key,
        )

    def schedule_task(
        self,
        session_id: SessionId,
        task_id: TaskId,
        *,
        payload: JsonMapping | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        available_at: datetime | None = None,
    ) -> WorkItem:
        _require_id(session_id, "session_id")
        _require_id(task_id, "task_id")
        return self._queue.enqueue(
            kind=WorkKind.TASK.value,
            payload=immutable_json(payload),
            session_id=session_id,
            task_id=task_id,
            priority=priority,
            max_attempts=max_attempts,
            available_at=available_at,
            idempotency_key=_task_key(session_id, task_id),
        )

    def schedule_action(
        self,
        session_id: SessionId,
        task_id: TaskId,
        action_id: ActionId,
        *,
        payload: JsonMapping | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        available_at: datetime | None = None,
    ) -> WorkItem:
        _require_id(session_id, "session_id")
        _require_id(task_id, "task_id")
        _require_id(action_id, "action_id")
        return self._queue.enqueue(
            kind=WorkKind.TOOL_ACTION.value,
            payload=immutable_json(payload),
            session_id=session_id,
            task_id=task_id,
            action_id=action_id,
            priority=priority,
            max_attempts=max_attempts,
            available_at=available_at,
            idempotency_key=_action_key(session_id, task_id, action_id),
        )

    def cancel_session(
        self,
        session_id: SessionId,
        *,
        reason: str = "session cancelled",
    ) -> tuple[WorkItem, ...]:
        _require_id(session_id, "session_id")
        return tuple(
            self._queue.cancel(item.work_item_id, reason=reason)
            for item in self._queue.list()
            if item.session_id == session_id and _can_cancel(item)
        )

    def cancel_task(
        self,
        session_id: SessionId,
        task_id: TaskId,
        *,
        reason: str = "task cancelled",
    ) -> tuple[WorkItem, ...]:
        _require_id(session_id, "session_id")
        _require_id(task_id, "task_id")
        return tuple(
            self._queue.cancel(item.work_item_id, reason=reason)
            for item in self._queue.list()
            if item.session_id == session_id and item.task_id == task_id and _can_cancel(item)
        )


def _session_key(session_id: SessionId) -> str:
    return f"session:{session_id}"


def _task_key(session_id: SessionId, task_id: TaskId) -> str:
    return f"task:{session_id}:{task_id}"


def _action_key(session_id: SessionId, task_id: TaskId, action_id: ActionId) -> str:
    return f"action:{session_id}:{task_id}:{action_id}"


def _require_id(value: object, name: str) -> None:
    parse_non_empty_string(str(value), name)


def _can_cancel(item: WorkItem) -> bool:
    return item.status not in {
        WorkItemStatus.COMPLETED,
        WorkItemStatus.FAILED,
        WorkItemStatus.CANCELLED,
    }
