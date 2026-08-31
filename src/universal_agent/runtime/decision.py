from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol, runtime_checkable

from universal_agent.core import (
    Decision,
    DecisionContext,
    DecisionType,
)
from universal_agent.model import ModelAdapter


class DecisionError(ValueError):
    pass


@runtime_checkable
class DecisionEngine(Protocol):
    async def decide(self, context: DecisionContext) -> Decision: ...


DecisionRule = Callable[[DecisionContext], Decision | None]


class ModelBackedDecisionEngine:
    def __init__(self, model: ModelAdapter) -> None:
        self._model = model

    async def decide(self, context: DecisionContext) -> Decision:
        try:
            decision = await self._model.decide(context)
        except Exception as exc:
            raise DecisionError(f"model failed to produce a decision: {exc}") from exc
        try:
            decision.validate()
        except ValueError as exc:
            raise DecisionError(f"model produced an invalid decision: {exc}") from exc
        return decision


class RuleBasedDecisionEngine:
    def __init__(
        self,
        rules: Iterable[DecisionRule] | None = None,
        *,
        fallback: DecisionEngine | None = None,
    ) -> None:
        self._rules = tuple(rules or ())
        self._fallback = fallback

    async def decide(self, context: DecisionContext) -> Decision:
        for rule in self._rules:
            decision = rule(context)
            if decision is None:
                continue
            try:
                decision.validate()
            except ValueError as exc:
                raise DecisionError(f"rule produced an invalid decision: {exc}") from exc
            return decision
        if self._fallback is not None:
            return await self._fallback.decide(context)
        raise DecisionError("no rule matched the decision context")


def ask_user_when_no_capability(context: DecisionContext) -> Decision | None:
    if context.capabilities:
        return None
    return Decision(
        DecisionType.ASK_USER,
        "no capabilities available to act on the current task",
        message="current task requires a capability that is not available",
    )


def recover_when_no_capability(context: DecisionContext) -> Decision | None:
    if context.capabilities:
        return None
    return Decision(
        DecisionType.FINISH,
        "no capabilities available to act on the current task",
    )
