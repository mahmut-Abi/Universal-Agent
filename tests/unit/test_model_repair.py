"""Tests for model-output repair: deterministic JSON salvage + repair re-ask.

Free/weak models frequently return malformed JSON, prose-wrapped JSON, or
decisions that violate the runtime schema. The chat-completions adapter owns
recovery for these provider-level defects (AGENTS.md §9: adapter owns model
invocation robustness; the runtime stays responsible for control flow):

1. Deterministic salvage — extract a balanced JSON object from prose.
2. Repair re-ask — feed the malformed output back with a corrective message,
   up to ``max_repair_retries`` times.
3. Transient failures are never repaired here; the runtime owns their retry.
"""

from __future__ import annotations

from typing import cast

import pytest

from universal_agent.core import (
    CapabilityCategory,
    CapabilitySummary,
    DecisionContext,
    DecisionType,
    GoalId,
    RiskLevel,
    SessionId,
    SuccessCriterion,
    TaskId,
    immutable_json,
)
from universal_agent.model.openai_adapters import OpenAIChatCompletionsModelAdapter
from universal_agent.model.openai_transport import OpenAIModelTransport
from universal_agent_api.types import JsonMapping


def context() -> DecisionContext:
    return DecisionContext(
        session_id=SessionId("session-1"),
        goal_id=GoalId("goal-1"),
        goal_description="Verify workload health",
        task_id=TaskId("task-1"),
        task_description="Inspect workload",
        iteration=2,
        satisfied_criteria=immutable_json({"healthy": False}),
        latest_observation=None,
        capabilities=(
            CapabilitySummary(
                "inspect_workload",
                "Inspect a workload",
                CapabilityCategory.OBSERVATION,
                RiskLevel.LOW,
                required_arguments=("name",),
                argument_schema=immutable_json(
                    {
                        "required": ["name"],
                        "properties": {"name": {"type": "string", "minLength": 1}},
                    }
                ),
            ),
        ),
        goal_success_criteria=(SuccessCriterion("healthy", True),),
        current_task_required_criteria=("healthy",),
        policy_summary=("read-only",),
)


VALID_DECISION = {
    "type": "execute",
    "reason": "Need current workload health.",
    "capability": "inspect_workload",
    "target": "deployment/api",
    "arguments": {"name": "api"},
    "expected_observations": ["healthy"],
    "message": None,
}


def chat_response(content: str) -> JsonMapping:
    return immutable_json(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
        }
    )


class ScriptedTransport:
    """Returns pre-scripted HTTP payloads in order; records every request."""

    def __init__(self, responses: list[JsonMapping]) -> None:
        self._responses = list(responses)
        self.requests: list[JsonMapping] = []

    async def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping:
        self.requests.append(payload)
        return self._responses.pop(0)


def build_adapter(
    transport: ScriptedTransport, *, max_repair_retries: int = 2
) -> OpenAIChatCompletionsModelAdapter:
    return OpenAIChatCompletionsModelAdapter(
        "gpt-runtime",
        api_key="openai-secret",
        transport=cast(OpenAIModelTransport, transport),
        response_format="prompt_json",
        max_repair_retries=max_repair_retries,
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_repair_reask_recovers_from_malformed_json() -> None:
    import json

    bad = "I cannot produce valid JSON right now, sorry!"
    good = json.dumps(VALID_DECISION)
    transport = ScriptedTransport([chat_response(bad), chat_response(good)])
    adapter = build_adapter(transport)

    decision = await adapter.decide(context())

    assert decision.type is DecisionType.EXECUTE
    assert decision.capability == "inspect_workload"
    assert len(transport.requests) == 2
    # The repair round must carry the bad output and the corrective prompt.
    repaired_messages = cast(list[dict[str, str]], transport.requests[1]["messages"])
    assert any(m["role"] == "assistant" for m in repaired_messages)
    assert any("was not a valid" in m["content"] for m in repaired_messages)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_prose_wrapped_json_is_salvaged_without_repair_round() -> None:
    import json

    wrapped = f"Here is the decision you asked for:\n{json.dumps(VALID_DECISION)}\nThanks!"
    transport = ScriptedTransport([chat_response(wrapped)])
    adapter = build_adapter(transport)

    decision = await adapter.decide(context())

    assert decision.type is DecisionType.EXECUTE
    assert len(transport.requests) == 1  # deterministic salvage, no re-ask


@pytest.mark.asyncio
@pytest.mark.unit
async def test_repair_retries_are_exhausted_then_fail() -> None:
    transport = ScriptedTransport(
        [chat_response("not json")] * 3  # initial + 2 repair rounds
    )
    adapter = build_adapter(transport, max_repair_retries=2)

    from universal_agent.model.errors import JsonHttpModelError

    with pytest.raises(JsonHttpModelError):
        await adapter.decide(context())
    assert len(transport.requests) == 3


@pytest.mark.asyncio
@pytest.mark.unit
async def test_zero_repair_retries_fails_fast() -> None:
    transport = ScriptedTransport([chat_response("not json")])
    adapter = build_adapter(transport, max_repair_retries=0)

    from universal_agent.model.errors import JsonHttpModelError

    with pytest.raises(JsonHttpModelError):
        await adapter.decide(context())
    assert len(transport.requests) == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_repair_loop_carries_growing_history() -> None:
    """Each repair round appends to the history instead of replacing it."""
    transport = ScriptedTransport(
        [
            chat_response("bad one"),
            chat_response("bad two"),
            chat_response('{"type": "finish", "reason": "done"}'),
        ]
    )
    adapter = build_adapter(transport, max_repair_retries=2)

    decision = await adapter.decide(context())

    assert decision.type is DecisionType.FINISH
    assert len(transport.requests) == 3
    first_messages = cast(list[object], transport.requests[0]["messages"])
    last_messages = cast(list[object], transport.requests[2]["messages"])
    assert len(last_messages) > len(first_messages)
