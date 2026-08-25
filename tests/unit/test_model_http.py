from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import pytest

from universal_agent.core import (
    CapabilityCategory,
    CapabilitySummary,
    DecisionContext,
    DecisionType,
    GoalId,
    JsonMapping,
    JsonValue,
    RiskLevel,
    SessionId,
    TaskId,
    immutable_json,
)
from universal_agent.model import (
    JsonHttpModelAdapter,
    JsonHttpModelError,
    ModelUsage,
    model_usage,
)


@dataclass(slots=True)
class RequestRecord:
    url: str
    headers: Mapping[str, str]
    payload: JsonMapping
    timeout_seconds: float


class RecordingTransport:
    def __init__(self, response: JsonMapping) -> None:
        self._response = response
        self.requests: list[RequestRecord] = []

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping:
        self.requests.append(RequestRecord(url, dict(headers), payload, timeout_seconds))
        return self._response


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
            ),
        ),
        policy_summary=("read-only",),
    )


@pytest.mark.asyncio
async def test_json_http_model_adapter_posts_context_and_decodes_decision_usage() -> None:
    transport = RecordingTransport(
        immutable_json(
            {
                "decision": {
                    "type": "execute",
                    "reason": "Need current workload health.",
                    "capability": "inspect_workload",
                    "target": "deployment/api",
                    "arguments": {"name": "api", "namespace": "prod"},
                    "expected_observations": ["healthy", "ready_replicas"],
                },
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 25,
                    "estimated_cost_micros": 42,
                    "currency": "USD",
                },
            }
        )
    )
    adapter = JsonHttpModelAdapter(
        "https://models.example.test/decide",
        "runtime-model",
        provider="test-provider",
        api_key="secret-token",
        extra_headers={"X-Agent-Runtime": "test"},
        timeout_seconds=4.5,
        transport=transport,
    )

    decision = await adapter.decide(context())

    assert decision.type is DecisionType.EXECUTE
    assert decision.capability == "inspect_workload"
    assert decision.target == "deployment/api"
    assert decision.arguments == {"name": "api", "namespace": "prod"}
    assert decision.expected_observations == ("healthy", "ready_replicas")
    assert model_usage(adapter) == ModelUsage(
        "test-provider",
        "runtime-model",
        input_tokens=100,
        output_tokens=25,
        estimated_cost_micros=42,
    )
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url == "https://models.example.test/decide"
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert request.headers["X-Agent-Runtime"] == "test"
    assert request.timeout_seconds == 4.5
    assert request.payload["model"] == "runtime-model"
    context_payload = cast(Mapping[str, JsonValue], request.payload["context"])
    assert context_payload["session_id"] == "session-1"
    assert context_payload["goal_description"] == "Verify workload health"
    assert context_payload["iteration"] == 2
    capabilities = cast(list[JsonValue], context_payload["capabilities"])
    first_capability = cast(Mapping[str, JsonValue], capabilities[0])
    assert first_capability["name"] == "inspect_workload"


@pytest.mark.asyncio
async def test_json_http_model_adapter_accepts_top_level_decision_without_usage() -> None:
    adapter = JsonHttpModelAdapter(
        "https://models.example.test/decide",
        "runtime-model",
        transport=RecordingTransport(
            immutable_json({"type": "finish", "reason": "All criteria satisfied."})
        ),
    )

    decision = await adapter.decide(context())

    assert decision.type is DecisionType.FINISH
    assert decision.reason == "All criteria satisfied."
    assert model_usage(adapter) is None


@pytest.mark.asyncio
async def test_json_http_model_adapter_rejects_invalid_decision_contract() -> None:
    adapter = JsonHttpModelAdapter(
        "https://models.example.test/decide",
        "runtime-model",
        transport=RecordingTransport(
            immutable_json(
                {
                    "decision": {
                        "type": "execute",
                        "reason": "Need inspection.",
                        "capability": "inspect_workload",
                    }
                }
            )
        ),
    )

    with pytest.raises(JsonHttpModelError, match="expected_observations"):
        await adapter.decide(context())


def test_json_http_model_adapter_validates_configuration() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        JsonHttpModelAdapter("", "runtime-model")
    with pytest.raises(ValueError, match="timeout"):
        JsonHttpModelAdapter(
            "https://models.example.test/decide", "runtime-model", timeout_seconds=0
        )
    with pytest.raises(ValueError, match="headers"):
        JsonHttpModelAdapter(
            "https://models.example.test/decide",
            "runtime-model",
            extra_headers={"X-Test\n": "bad"},
        )
