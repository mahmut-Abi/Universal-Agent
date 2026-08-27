from __future__ import annotations

from dataclasses import dataclass

from universal_agent.operations.helpers import non_negative_int, string
from universal_agent.operations.views import ModelCostBreakdownView, RuntimeCostView
from universal_agent.runtime import RuntimeEventView


def build_runtime_cost(events: tuple[RuntimeEventView, ...]) -> RuntimeCostView:
    accumulators: dict[tuple[str, str, str], _CostAccumulator] = {}
    for event in events:
        if event.type != "ModelUsageRecorded":
            continue
        provider = string(event.data.get("provider")) or "unknown"
        model = string(event.data.get("model")) or "unknown"
        currency = string(event.data.get("currency")) or "USD"
        key = (provider, model, currency)
        if key not in accumulators:
            accumulators[key] = _CostAccumulator(provider, model, currency)
        accumulators[key].add(
            input_tokens=non_negative_int(event.data.get("input_tokens")),
            output_tokens=non_negative_int(event.data.get("output_tokens")),
            estimated_cost_micros=non_negative_int(event.data.get("estimated_cost_micros")),
        )
    by_model = tuple(
        item.view()
        for item in sorted(
            accumulators.values(),
            key=lambda item: (item.provider, item.model, item.currency),
        )
    )
    return RuntimeCostView(
        model_call_count=sum(item.call_count for item in by_model),
        input_tokens=sum(item.input_tokens for item in by_model),
        output_tokens=sum(item.output_tokens for item in by_model),
        total_tokens=sum(item.total_tokens for item in by_model),
        estimated_cost_micros=sum(item.estimated_cost_micros for item in by_model),
        currency=_aggregate_currency(tuple(item.currency for item in by_model)),
        by_model=by_model,
    )


def _aggregate_currency(currencies: tuple[str, ...]) -> str:
    if not currencies:
        return "USD"
    unique = frozenset(currencies)
    if len(unique) == 1:
        return currencies[0]
    return "mixed"


@dataclass(slots=True)
class _CostAccumulator:
    provider: str
    model: str
    currency: str
    call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_micros: int = 0

    def add(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_micros: int,
    ) -> None:
        self.call_count += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.estimated_cost_micros += estimated_cost_micros

    def view(self) -> ModelCostBreakdownView:
        return ModelCostBreakdownView(
            provider=self.provider,
            model=self.model,
            call_count=self.call_count,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_tokens=self.input_tokens + self.output_tokens,
            estimated_cost_micros=self.estimated_cost_micros,
            currency=self.currency,
        )
