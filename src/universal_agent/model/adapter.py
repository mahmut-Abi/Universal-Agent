from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from typing import Protocol

from universal_agent.core import Decision, DecisionContext


class ModelAdapter(Protocol):
    async def decide(self, context: DecisionContext) -> Decision: ...


class ScriptedModelAdapter:
    """Deterministic model boundary for tests and examples."""

    def __init__(self, decisions: Iterable[Decision]) -> None:
        self._decisions = deque(decisions)
        self.contexts: list[DecisionContext] = []

    async def decide(self, context: DecisionContext) -> Decision:
        self.contexts.append(context)
        if not self._decisions:
            raise RuntimeError("scripted model has no decision remaining")
        return self._decisions.popleft()
