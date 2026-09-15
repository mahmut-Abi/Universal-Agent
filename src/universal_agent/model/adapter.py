from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from universal_agent.core import Decision, DecisionContext
from universal_agent.core.config_validation import (
    parse_non_empty_string,
    parse_non_negative_int,
)


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
    # Bounded, redacted request/response capture for trace detail (UA-CS-006).
    prompt: str = ""
    completion: str = ""

    def __post_init__(self) -> None:
        parse_non_empty_string(self.provider, "model usage provider")
        parse_non_empty_string(self.model, "model usage model")
        parse_non_negative_int(self.input_tokens, "model usage input_tokens")
        parse_non_negative_int(self.output_tokens, "model usage output_tokens")
        parse_non_negative_int(
            self.estimated_cost_micros,
            "model usage estimated_cost_micros",
        )
        parse_non_empty_string(self.currency, "model usage currency")

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


# P0 Golden Path alias: `FakeModel` names the deterministic offline model used by
# tests, doctor and the default profile (no real LLM required).
FakeModel = ScriptedModelAdapter


LLM_CAPTURE_MAX_CHARS = 4000


def bounded_llm_text(text: str, *, limit: int = LLM_CAPTURE_MAX_CHARS) -> str:
    """Bound captured LLM prompt/completion text for event-safe storage."""

    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"
