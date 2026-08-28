from __future__ import annotations

from collections import Counter

from universal_agent.operations.helpers import non_negative_int, string
from universal_agent.operations.views import ModelCostBreakdownView, RuntimeCostView
from universal_agent.runtime import RuntimeEventView

_CostKey = tuple[str, str, str]


def build_runtime_cost(events: tuple[RuntimeEventView, ...]) -> RuntimeCostView:
    call_counts: Counter[_CostKey] = Counter()
    input_tokens: Counter[_CostKey] = Counter()
    output_tokens: Counter[_CostKey] = Counter()
    estimated_cost_micros: Counter[_CostKey] = Counter()
    for event in events:
        if event.type != "ModelUsageRecorded":
            continue
        provider = string(event.data.get("provider")) or "unknown"
        model = string(event.data.get("model")) or "unknown"
        currency = string(event.data.get("currency")) or "USD"
        key = (provider, model, currency)
        call_counts[key] += 1
        input_tokens[key] += non_negative_int(event.data.get("input_tokens"))
        output_tokens[key] += non_negative_int(event.data.get("output_tokens"))
        estimated_cost_micros[key] += non_negative_int(event.data.get("estimated_cost_micros"))
    by_model = tuple(
        ModelCostBreakdownView(
            provider=provider,
            model=model,
            call_count=call_counts[key],
            input_tokens=input_tokens[key],
            output_tokens=output_tokens[key],
            total_tokens=input_tokens[key] + output_tokens[key],
            estimated_cost_micros=estimated_cost_micros[key],
            currency=currency,
        )
        for key in sorted(call_counts)
        for provider, model, currency in (key,)
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
