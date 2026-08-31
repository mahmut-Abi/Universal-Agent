from __future__ import annotations

from datetime import UTC, datetime

import pytest

from universal_agent.core import (
    ActionId,
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    Goal,
    Observation,
    ObservationId,
    ObservationStatus,
    SuccessCriterion,
    Task,
    TaskId,
    immutable_json,
)
from universal_agent.evaluation import CriteriaEvaluator, EvaluatorRegistry

FIXED_AT = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def make_observation() -> Observation:
    return Observation(
        id=ObservationId("observation-1"),
        action_id=ActionId("action-1"),
        task_id=TaskId("task-1"),
        source="cap:tool",
        status=ObservationStatus.SUCCEEDED,
        data=immutable_json({}),
        observed_at=FIXED_AT,
    )


def test_criteria_evaluator_name_is_fixed() -> None:
    assert CriteriaEvaluator.name == "criteria"


def test_criteria_completed_when_satisfied_criteria_match() -> None:
    evaluator = CriteriaEvaluator()
    goal = Goal("g", success_criteria=(SuccessCriterion("health", "ok"),))
    task = Task("t", required_criteria=("health",))
    context = EvaluationContext(
        goal,
        task,
        make_observation(),
        satisfied_criteria=immutable_json({"health": "ok"}),
    )

    result = evaluator.evaluate(context)

    assert isinstance(result, EvaluationResult)
    assert result.status == EvaluationStatus.COMPLETED
    assert result.task_completed is True
    assert result.goal_completed is True
    assert result.evaluator_name == "criteria"
    assert result.matched_criteria == immutable_json({"health": "ok"})


def test_criteria_incomplete_when_value_differs() -> None:
    evaluator = CriteriaEvaluator()
    goal = Goal("g", success_criteria=(SuccessCriterion("health", "ok"),))
    task = Task("t", required_criteria=("health",))
    context = EvaluationContext(
        goal,
        task,
        make_observation(),
        satisfied_criteria=immutable_json({"health": "degraded"}),
    )

    result = evaluator.evaluate(context)

    assert result.status == EvaluationStatus.INCOMPLETE
    assert result.task_completed is False
    assert result.goal_completed is False
    assert result.matched_criteria == immutable_json({})


def test_criteria_incomplete_when_task_requires_extra_criterion() -> None:
    evaluator = CriteriaEvaluator()
    goal = Goal("g", success_criteria=(SuccessCriterion("health", "ok"),))
    task = Task("t", required_criteria=("health", "scaled"))
    context = EvaluationContext(
        goal,
        task,
        make_observation(),
        satisfied_criteria=immutable_json({"health": "ok"}),
    )

    result = evaluator.evaluate(context)

    assert result.status == EvaluationStatus.INCOMPLETE
    assert result.task_completed is False
    assert result.goal_completed is True


def test_criteria_completed_when_no_required_criteria() -> None:
    evaluator = CriteriaEvaluator()
    goal = Goal("g", success_criteria=())
    task = Task("t", required_criteria=())
    context = EvaluationContext(
        goal,
        task,
        make_observation(),
        satisfied_criteria=immutable_json({}),
    )

    result = evaluator.evaluate(context)

    assert result.status == EvaluationStatus.COMPLETED
    assert result.task_completed is True
    assert result.goal_completed is True


def test_criteria_task_met_but_goal_unmet() -> None:
    evaluator = CriteriaEvaluator()
    goal = Goal(
        "g",
        success_criteria=(SuccessCriterion("health", "ok"), SuccessCriterion("scaled", "yes")),
    )
    task = Task("t", required_criteria=("health",))
    context = EvaluationContext(
        goal,
        task,
        make_observation(),
        satisfied_criteria=immutable_json({"health": "ok"}),
    )

    result = evaluator.evaluate(context)

    assert result.status == EvaluationStatus.INCOMPLETE
    assert result.task_completed is True
    assert result.goal_completed is False
    assert result.matched_criteria == immutable_json({"health": "ok"})


def test_matched_criteria_excludes_keys_outside_expected() -> None:
    evaluator = CriteriaEvaluator()
    goal = Goal("g", success_criteria=(SuccessCriterion("health", "ok"),))
    task = Task("t", required_criteria=("health",))
    context = EvaluationContext(
        goal,
        task,
        make_observation(),
        satisfied_criteria=immutable_json({"health": "ok", "unrelated": "value"}),
    )

    result = evaluator.evaluate(context)

    assert result.matched_criteria == immutable_json({"health": "ok"})


def test_evaluator_registry_registers_and_resolves() -> None:
    registry = EvaluatorRegistry()
    evaluator = CriteriaEvaluator()
    registry.register(evaluator)

    assert registry.resolve("criteria") is evaluator


def test_evaluator_registry_rejects_duplicate_name() -> None:
    registry = EvaluatorRegistry()
    registry.register(CriteriaEvaluator())

    with pytest.raises(ValueError, match="evaluator already registered: criteria"):
        registry.register(CriteriaEvaluator())


def test_evaluator_registry_resolve_unknown_raises_lookup_error() -> None:
    registry = EvaluatorRegistry()

    with pytest.raises(LookupError, match="unknown evaluator: missing"):
        registry.resolve("missing")
