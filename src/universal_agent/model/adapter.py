from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from universal_agent.core import Decision, DecisionContext


class ModelAdapter(Protocol):
    async def decide(self, context: DecisionContext) -> Decision: ...


@dataclass(frozen=True, slots=True)
class ModelUsage:
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_micros: int = 0
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ValueError("model usage provider must not be empty")
        if not self.model.strip():
            raise ValueError("model usage model must not be empty")
        if self.input_tokens < 0:
            raise ValueError("model usage input_tokens must not be negative")
        if self.output_tokens < 0:
            raise ValueError("model usage output_tokens must not be negative")
        if self.estimated_cost_micros < 0:
            raise ValueError("model usage estimated_cost_micros must not be negative")
        if not self.currency.strip():
            raise ValueError("model usage currency must not be empty")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@runtime_checkable
class ModelUsageProvider(Protocol):
    def model_usage(self) -> ModelUsage | None: ...


def model_usage(adapter: ModelAdapter) -> ModelUsage | None:
    if isinstance(adapter, ModelUsageProvider):
        return adapter.model_usage()
    return None


class ScriptedModelAdapter:
    """Deterministic model boundary for tests and examples."""

    def __init__(
        self,
        decisions: Iterable[Decision],
        *,
        usage: Iterable[ModelUsage] = (),
    ) -> None:
        self._decisions = deque(decisions)
        self._usage = deque(usage)
        self._last_usage: ModelUsage | None = None
        self.contexts: list[DecisionContext] = []

    async def decide(self, context: DecisionContext) -> Decision:
        self.contexts.append(context)
        if not self._decisions:
            raise RuntimeError("scripted model has no decision remaining")
        decision = self._decisions.popleft()
        self._last_usage = self._usage.popleft() if self._usage else None
        return decision

    def model_usage(self) -> ModelUsage | None:
        return self._last_usage
