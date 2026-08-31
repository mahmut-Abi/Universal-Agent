from __future__ import annotations

import pytest

from universal_agent.core import (
    Decision,
    DecisionContext,
    DecisionType,
    GoalId,
    SessionId,
    TaskId,
    immutable_json,
)
from universal_agent.model import ModelUsage, ScriptedModelAdapter, model_usage


def context() -> DecisionContext:
    return DecisionContext(
        session_id=SessionId("session-1"),
        goal_id=GoalId("goal-1"),
        goal_description="Verify workload health",
        task_id=TaskId("task-1"),
        task_description="Inspect workload",
        iteration=1,
        satisfied_criteria=immutable_json(),
        latest_observation=None,
        capabilities=(),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "done")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_scripted_model_adapter_exposes_last_usage_record() -> None:
    usage = ModelUsage(
        provider="scripted",
        model="fixture-model",
        input_tokens=120,
        output_tokens=30,
        estimated_cost_micros=45,
    )
    adapter = ScriptedModelAdapter([finish()], usage=[usage])

    decision = await adapter.decide(context())

    assert decision.type is DecisionType.FINISH
    assert model_usage(adapter) == usage
    assert usage.total_tokens == 150


@pytest.mark.unit
def test_model_usage_rejects_negative_values() -> None:
    with pytest.raises(ValueError, match="model usage input_tokens must not be negative"):
        ModelUsage("provider", "model", input_tokens=-1)

    with pytest.raises(ValueError, match="model usage output_tokens must not be negative"):
        ModelUsage("provider", "model", output_tokens=-1)

    with pytest.raises(
        ValueError,
        match="model usage estimated_cost_micros must not be negative",
    ):
        ModelUsage("provider", "model", estimated_cost_micros=-1)


@pytest.mark.unit
def test_model_usage_uses_strict_pydantic_scalar_validation() -> None:
    with pytest.raises(ValueError, match="model usage provider must not be empty"):
        ModelUsage(" ", "model")

    with pytest.raises(ValueError, match="model usage currency must not be empty"):
        ModelUsage("provider", "model", currency="")

    with pytest.raises(ValueError, match="model usage input_tokens must be an integer"):
        ModelUsage("provider", "model", input_tokens=True)
