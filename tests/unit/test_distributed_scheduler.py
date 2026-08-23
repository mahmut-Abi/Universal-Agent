from __future__ import annotations

from datetime import UTC, datetime

import pytest

from universal_agent.core import ActionId, SessionId, TaskId, immutable_json
from universal_agent.distributed import (
    InMemoryWorkQueue,
    WorkerId,
    WorkItemStatus,
    WorkKind,
    WorkScheduler,
)


def test_work_scheduler_schedules_session_work_idempotently() -> None:
    queue = InMemoryWorkQueue()
    scheduler = WorkScheduler(queue)
    session_id = SessionId("session-1")
    now = datetime(2026, 1, 1, tzinfo=UTC)

    first = scheduler.schedule_session(
        session_id,
        payload=immutable_json({"goal": "inspect workload"}),
        priority=5,
        available_at=now,
    )
    second = scheduler.schedule_session(
        session_id,
        payload=immutable_json({"goal": "duplicate"}),
        priority=1,
        available_at=now,
    )

    assert first == second
    assert first.kind == WorkKind.AGENT_SESSION.value
    assert first.session_id == session_id
    assert first.idempotency_key == "session:session-1"
    assert len(queue.queued()) == 1


def test_work_scheduler_schedules_goal_work_idempotently() -> None:
    queue = InMemoryWorkQueue()
    scheduler = WorkScheduler(queue)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    payload = immutable_json({"goal": {"description": "verify workload"}})

    first = scheduler.schedule_goal(
        payload=payload,
        idempotency_key="goal:verify-workload",
        priority=5,
        available_at=now,
    )
    second = scheduler.schedule_goal(
        payload=immutable_json({"goal": {"description": "duplicate"}}),
        idempotency_key="goal:verify-workload",
        priority=1,
        available_at=now,
    )

    assert first == second
    assert first.kind == WorkKind.AGENT_GOAL.value
    assert first.session_id is None
    assert first.idempotency_key == "goal:verify-workload"
    assert first.payload == payload
    assert len(queue.queued()) == 1


def test_work_scheduler_preserves_task_and_action_identity() -> None:
    queue = InMemoryWorkQueue()
    scheduler = WorkScheduler(queue)
    session_id = SessionId("session-1")
    task_id = TaskId("task-1")
    action_id = ActionId("action-1")

    task = scheduler.schedule_task(
        session_id,
        task_id,
        payload=immutable_json({"description": "diagnose"}),
    )
    action = scheduler.schedule_action(
        session_id,
        task_id,
        action_id,
        payload=immutable_json({"capability": "inspect_workload"}),
    )

    assert task.kind == WorkKind.TASK.value
    assert task.session_id == session_id
    assert task.task_id == task_id
    assert task.action_id is None
    assert task.idempotency_key == "task:session-1:task-1"
    assert action.kind == WorkKind.TOOL_ACTION.value
    assert action.session_id == session_id
    assert action.task_id == task_id
    assert action.action_id == action_id
    assert action.idempotency_key == "action:session-1:task-1:action-1"


def test_work_scheduler_cancels_session_scope_without_touching_terminal_items() -> None:
    queue = InMemoryWorkQueue()
    scheduler = WorkScheduler(queue)
    session_id = SessionId("session-1")
    other_session_id = SessionId("session-2")
    task_id = TaskId("task-1")
    now = datetime(2026, 1, 1, tzinfo=UTC)

    queued = scheduler.schedule_session(session_id, available_at=now)
    leased = scheduler.schedule_task(session_id, task_id, priority=10, available_at=now)
    action = scheduler.schedule_action(
        session_id,
        task_id,
        ActionId("action-1"),
        priority=5,
        available_at=now,
    )
    scheduler.schedule_session(other_session_id, available_at=now)

    leased_item = queue.lease(worker_id=WorkerId("worker-a"), now=now)
    assert leased_item.work_item_id == leased.work_item_id
    assert leased_item.lease is not None
    queue.complete(leased_item.lease.lease_id, worker_id=WorkerId("worker-a"), now=now)
    assert queue.get(action.work_item_id).status is WorkItemStatus.QUEUED

    cancelled = scheduler.cancel_session(session_id, reason="user cancelled")

    assert {item.work_item_id for item in cancelled} == {
        queued.work_item_id,
        action.work_item_id,
    }
    assert queue.get(queued.work_item_id).status is WorkItemStatus.CANCELLED
    assert queue.get(leased.work_item_id).status is WorkItemStatus.COMPLETED
    assert queue.get(action.work_item_id).status is WorkItemStatus.CANCELLED
    assert len([item for item in queue.queued() if item.session_id == other_session_id]) == 1


def test_work_scheduler_cancels_task_scope_only() -> None:
    queue = InMemoryWorkQueue()
    scheduler = WorkScheduler(queue)
    session_id = SessionId("session-1")
    task_id = TaskId("task-1")
    other_task_id = TaskId("task-2")

    matching = scheduler.schedule_task(session_id, task_id)
    other = scheduler.schedule_task(session_id, other_task_id)

    cancelled = scheduler.cancel_task(session_id, task_id, reason="task cancelled")

    assert [item.work_item_id for item in cancelled] == [matching.work_item_id]
    assert queue.get(matching.work_item_id).status is WorkItemStatus.CANCELLED
    assert queue.get(other.work_item_id).status is WorkItemStatus.QUEUED


def test_work_scheduler_rejects_empty_runtime_ids() -> None:
    scheduler = WorkScheduler(InMemoryWorkQueue())

    with pytest.raises(ValueError, match="session_id"):
        scheduler.schedule_session(SessionId(""))
    with pytest.raises(ValueError, match="task_id"):
        scheduler.schedule_task(SessionId("session-1"), TaskId(""))
    with pytest.raises(ValueError, match="action_id"):
        scheduler.schedule_action(SessionId("session-1"), TaskId("task-1"), ActionId(""))
