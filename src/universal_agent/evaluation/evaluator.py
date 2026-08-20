from __future__ import annotations

from typing import Protocol

from universal_agent.core import (
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    immutable_json,
)


class Evaluator(Protocol):
    @property
    def name(self) -> str: ...

    def evaluate(self, context: EvaluationContext) -> EvaluationResult: ...


class CriteriaEvaluator:
    name = "criteria"

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        expected = {
            criterion.key: criterion.expected for criterion in context.goal.success_criteria
        }
        matched = {
            key: value
            for key, value in context.satisfied_criteria.items()
            if key in expected and value == expected[key]
        }
        task_complete = all(key in matched for key in context.task.required_criteria)
        goal_complete = all(key in matched for key in expected)
        if task_complete and goal_complete:
            return EvaluationResult(
                EvaluationStatus.COMPLETED,
                "task and goal criteria satisfied",
                self.name,
                immutable_json(matched),
                True,
                True,
            )
        return EvaluationResult(
            EvaluationStatus.INCOMPLETE,
            "required criteria are not yet satisfied",
            self.name,
            immutable_json(matched),
            task_complete,
            goal_complete,
        )


class EvaluatorRegistry:
    def __init__(self) -> None:
        self._evaluators: dict[str, Evaluator] = {}

    def register(self, evaluator: Evaluator) -> None:
        if evaluator.name in self._evaluators:
            raise ValueError(f"evaluator already registered: {evaluator.name}")
        self._evaluators[evaluator.name] = evaluator

    def resolve(self, name: str) -> Evaluator:
        try:
            return self._evaluators[name]
        except KeyError as exc:
            raise LookupError(f"unknown evaluator: {name}") from exc
