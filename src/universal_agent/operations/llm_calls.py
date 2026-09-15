"""LLM call projection for session detail surfaces (UA-CS-006).

Projects ``LLMCallRecorded`` events into the per-call records the web chat
detail views render: provider/model, bounded prompt/completion text, token
accounting and cost. Pure projection over RuntimeEventView — no runtime state.
"""

from __future__ import annotations

from typing import cast

from universal_agent.core import JsonMapping, to_json_object
from universal_agent.runtime import RuntimeEventView

__all__ = ["llm_calls_body"]


def _safe_int(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def llm_calls_body(events: tuple[RuntimeEventView, ...]) -> JsonMapping:
    """JSON-safe LLM call records for one session (newest last)."""

    calls: list[JsonMapping] = []
    for event in events:
        if event.type != "LLMCallRecorded":
            continue
        data = event.data
        calls.append(
            cast(
                JsonMapping,
                to_json_object(
                    {
                        "occurred_at": event.occurred_at,
                        "provider": data.get("provider"),
                        "model": data.get("model"),
                        "prompt": data.get("prompt", ""),
                        "completion": data.get("completion", ""),
                        "input_tokens": data.get("input_tokens", 0),
                        "output_tokens": data.get("output_tokens", 0),
                        "total_tokens": data.get("total_tokens", 0),
                        "estimated_cost_micros": data.get("estimated_cost_micros", 0),
                        "currency": data.get("currency", "USD"),
                    },
                    fallback_to_string=True,
                ),
            )
        )
    total_tokens = sum(_safe_int(call.get("total_tokens", 0)) for call in calls)
    total_cost = sum(_safe_int(call.get("estimated_cost_micros", 0)) for call in calls)
    return cast(
        JsonMapping,
        to_json_object(
            {
                "call_count": len(calls),
                "total_tokens": total_tokens,
                "total_estimated_cost_micros": total_cost,
                "calls": calls,
            },
            fallback_to_string=True,
        ),
    )
