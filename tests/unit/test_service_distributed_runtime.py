from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

import universal_agent.service.distributed_runtime as distributed_runtime
from universal_agent.core import (
    ExecutionStatus,
    Goal,
    GoalId,
    GoalStatus,
    JsonValue,
    SessionId,
    SuccessCriterion,
    Task,
    TaskId,
    immutable_json,
)
from universal_agent.distributed import (
    DistributedLockConflictError,
    DistributedLockOwnerId,
    DistributedRuntimeCoordinator,
    WorkerId,
    WorkerRunStatus,
)
from universal_agent.runtime import RuntimeAPI
from universal_agent.service.distributed_runtime import (
    DistributedRuntimeController,
    distributed_session_lock_key,
    goal_task_from_work_payload,
    goal_work_payload,
)


@pytest.mark.contract
def test_goal_work_payload_round_trips_through_pydantic_decoder() -> None:
    goal_created_at = datetime(2026, 1, 1, 8, 30, tzinfo=UTC)
    task_created_at = datetime(2026, 1, 1, 8, 31, tzinfo=UTC)
    goal = Goal(
        "Diagnose workload",
        (SuccessCriterion("healthy", True),),
        id=GoalId("goal-1"),
        created_at=goal_created_at,
    )
    task = Task(
        "Inspect deployment",
        ("healthy",),
        id=TaskId("task-1"),
        created_at=task_created_at,
    )

    restored_goal, restored_task = goal_task_from_work_payload(goal_work_payload(goal, task))

    assert restored_goal.id == GoalId("goal-1")
    assert restored_goal.description == "Diagnose workload"
    assert restored_goal.success_criteria == (SuccessCriterion("healthy", True),)
    assert restored_goal.created_at == goal_created_at
    assert restored_task.id == TaskId("task-1")
    assert restored_task.description == "Inspect deployment"
    assert restored_task.required_criteria == ("healthy",)
    assert restored_task.created_at == task_created_at


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        ({"goal": "bad", "task": {}}, "goal must be an object"),
        (
            {
                "goal": {
                    "id": "goal-1",
                    "description": " ",
                    "success_criteria": [{"key": "healthy", "expected": True}],
                },
                "task": {
                    "id": "task-1",
                    "description": "Inspect deployment",
                    "required_criteria": ["healthy"],
                },
            },
            "goal.description must not be empty",
        ),
        (
            {
                "goal": {
                    "id": "goal-1",
                    "description": "Diagnose workload",
                    "success_criteria": [],
                },
                "task": {
                    "id": "task-1",
                    "description": "Inspect deployment",
                    "required_criteria": ["healthy"],
                },
            },
            "goal.success_criteria must not be empty",
        ),
        (
            {
                "goal": {
                    "id": "goal-1",
                    "description": "Diagnose workload",
                    "success_criteria": [{"key": "healthy"}],
                },
                "task": {
                    "id": "task-1",
                    "description": "Inspect deployment",
                    "required_criteria": ["healthy"],
                },
            },
            "goal.success_criteria[0].expected is required",
        ),
        (
            {
                "goal": {
                    "id": "goal-1",
                    "description": "Diagnose workload",
                    "success_criteria": [{"key": "", "expected": True}],
                },
                "task": {
                    "id": "task-1",
                    "description": "Inspect deployment",
                    "required_criteria": ["healthy"],
                },
            },
            "goal.success_criteria[0].key must not be empty",
        ),
        (
            {
                "goal": {
                    "id": "goal-1",
                    "description": "Diagnose workload",
                    "success_criteria": [{"key": "healthy", "expected": True}],
                },
                "task": {
                    "id": "task-1",
                    "description": "Inspect deployment",
                    "required_criteria": [1],
                },
            },
            "task.required_criteria[0] must be a string",
        ),
        (
            {
                "goal": {
                    "id": "goal-1",
                    "description": "Diagnose workload",
                    "success_criteria": [{"key": "healthy", "expected": True}],
                },
                "task": {
                    "id": "task-1",
                    "description": "Inspect deployment",
                    "required_criteria": [" "],
                },
            },
            "task.required_criteria[0] must not be empty",
        ),
    ),
)
@pytest.mark.contract
def test_goal_task_from_work_payload_reports_stable_validation_messages(
    payload: dict[str, JsonValue],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        goal_task_from_work_payload(immutable_json(payload))


@pytest.mark.asyncio
@pytest.mark.unit
async def test_distributed_session_lock_is_heartbeated_while_resume_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(distributed_runtime, "_DISTRIBUTED_SESSION_LOCK_TTL_SECONDS", 0.08)
    coordinator = DistributedRuntimeCoordinator()
    session_id = SessionId("session-1")
    scheduled = coordinator.schedule_session(session_id).scheduled_work_item
    competing_attempts: list[str] = []

    class SlowRuntimeAPI:
        async def get_session(self, loaded_session_id: SessionId) -> object:
            assert loaded_session_id == session_id
            return SimpleNamespace(pending_action=None, goal_status=GoalStatus.WAITING)

        async def resume_session(
            self,
            loaded_session_id: SessionId,
            *,
            confirmed: bool | None = None,
        ) -> object:
            assert loaded_session_id == session_id
            assert confirmed is None
            await asyncio.sleep(0.11)
            try:
                lease = coordinator.locks.acquire(
                    lock_key=distributed_session_lock_key(session_id),
                    owner_id=DistributedLockOwnerId("worker-b"),
                    ttl_seconds=0.08,
                )
            except DistributedLockConflictError:
                competing_attempts.append("blocked")
            else:
                competing_attempts.append("acquired")
                coordinator.locks.release(
                    lease.lease_id,
                    owner_id=DistributedLockOwnerId("worker-b"),
                )
            return SimpleNamespace(
                result=SimpleNamespace(
                    status=ExecutionStatus.WAITING,
                    reason="still waiting",
                )
            )

    controller = DistributedRuntimeController(
        runtime_api=cast(RuntimeAPI, SlowRuntimeAPI()),
        coordinator=coordinator,
    )

    result = await controller.run_worker_once(
        WorkerId("worker-a"),
        lease_ttl_seconds=0.2,
        worker_ttl_seconds=0.2,
        heartbeat_interval_seconds=0.02,
    )

    assert result is not None
    assert result.status is WorkerRunStatus.COMPLETED
    assert competing_attempts == ["blocked"]
    assert coordinator.queue.get(scheduled.work_item_id).status.value == "completed"
