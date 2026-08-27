from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from universal_agent.core import (
    Goal,
    GoalId,
    JsonValue,
    SuccessCriterion,
    Task,
    TaskId,
    immutable_json,
)
from universal_agent.service.distributed_runtime import (
    goal_task_from_work_payload,
    goal_work_payload,
)


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
    ),
)
def test_goal_task_from_work_payload_reports_stable_validation_messages(
    payload: dict[str, JsonValue],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        goal_task_from_work_payload(immutable_json(payload))
