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
    SuccessCriterion,
    TaskId,
    immutable_json,
)
from universal_agent.model import (
    JsonHttpModelAdapter,
    JsonHttpModelError,
    ModelUsage,
    OpenAIChatCompletionsModelAdapter,
    OpenAIResponsesModelAdapter,
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
    assert context_payload["goal_success_criteria"] == [{"key": "healthy", "expected": True}]
    assert context_payload["current_task_required_criteria"] == ["healthy"]
    assert context_payload["iteration"] == 2
    capabilities = cast(list[JsonValue], context_payload["capabilities"])
    first_capability = cast(Mapping[str, JsonValue], capabilities[0])
    assert first_capability["name"] == "inspect_workload"
    assert first_capability["required_arguments"] == ["name"]
    assert first_capability["argument_schema"] == {
        "required": ["name"],
        "properties": {"name": {"type": "string", "minLength": 1}},
    }


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


@pytest.mark.asyncio
async def test_json_http_model_adapter_rejects_decision_outside_context_capabilities() -> None:
    adapter = JsonHttpModelAdapter(
        "https://models.example.test/decide",
        "runtime-model",
        transport=RecordingTransport(
            immutable_json(
                {
                    "decision": {
                        "type": "execute",
                        "reason": "Try unavailable capability.",
                        "capability": "scale_workload",
                        "arguments": {"name": "api"},
                        "expected_observations": ["scaled"],
                    }
                }
            )
        ),
    )

    with pytest.raises(JsonHttpModelError, match="capability is not available in context"):
        await adapter.decide(context())


@pytest.mark.asyncio
async def test_json_http_model_adapter_rejects_context_argument_contract_violation() -> None:
    adapter = JsonHttpModelAdapter(
        "https://models.example.test/decide",
        "runtime-model",
        transport=RecordingTransport(
            immutable_json(
                {
                    "decision": {
                        "type": "execute",
                        "reason": "Missing required workload name.",
                        "capability": "inspect_workload",
                        "arguments": {},
                        "expected_observations": ["healthy"],
                    }
                }
            )
        ),
    )

    with pytest.raises(JsonHttpModelError, match="missing required arguments: name"):
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


@pytest.mark.asyncio
async def test_openai_chat_completions_model_adapter_posts_structured_request() -> None:
    transport = RecordingTransport(
        immutable_json(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json_text(
                                {
                                    "type": "execute",
                                    "reason": "Need current workload health.",
                                    "capability": "inspect_workload",
                                    "target": "deployment/api",
                                    "arguments": {"name": "api", "namespace": "prod"},
                                    "expected_observations": ["healthy", "ready_replicas"],
                                    "message": None,
                                }
                            ),
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 130,
                    "completion_tokens": 35,
                },
            }
        )
    )
    adapter = OpenAIChatCompletionsModelAdapter(
        "gpt-runtime",
        api_key="openai-secret",
        endpoint="https://api.openai.example.test/v1/chat/completions",
        extra_headers={"OpenAI-Organization": "org-test"},
        timeout_seconds=5.0,
        transport=transport,
    )

    decision = await adapter.decide(context())

    assert decision.type is DecisionType.EXECUTE
    assert decision.capability == "inspect_workload"
    assert decision.arguments == {"name": "api", "namespace": "prod"}
    assert model_usage(adapter) == ModelUsage(
        "openai_chat_completions",
        "gpt-runtime",
        input_tokens=130,
        output_tokens=35,
    )
    request = transport.requests[0]
    assert request.url == "https://api.openai.example.test/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer openai-secret"
    assert request.headers["OpenAI-Organization"] == "org-test"
    assert request.payload["model"] == "gpt-runtime"
    messages = cast(list[JsonValue], request.payload["messages"])
    assert len(messages) == 2
    user_message = cast(Mapping[str, JsonValue], messages[1])
    prompt_text = cast(str, user_message["content"])
    assert "Verify workload health" in prompt_text
    assert "required_arguments" in prompt_text
    assert "argument_schema" in prompt_text
    assert "goal_success_criteria" in prompt_text
    response_format = cast(Mapping[str, JsonValue], request.payload["response_format"])
    assert response_format["type"] == "json_schema"
    schema_payload = cast(Mapping[str, JsonValue], response_format["json_schema"])
    assert schema_payload["strict"] is True
    schema = cast(Mapping[str, JsonValue], schema_payload["schema"])
    assert schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_openai_chat_completions_model_adapter_rejects_tool_call_finish() -> None:
    adapter = OpenAIChatCompletionsModelAdapter(
        "gpt-runtime",
        api_key="openai-secret",
        transport=RecordingTransport(
            immutable_json(
                {
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {"role": "assistant", "content": ""},
                        }
                    ]
                }
            )
        ),
    )

    with pytest.raises(JsonHttpModelError, match="did not return final JSON content"):
        await adapter.decide(context())


@pytest.mark.asyncio
async def test_openai_chat_completions_model_adapter_rejects_context_argument_violation() -> None:
    adapter = OpenAIChatCompletionsModelAdapter(
        "gpt-runtime",
        api_key="openai-secret",
        transport=RecordingTransport(
            immutable_json(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json_text(
                                    {
                                        "type": "execute",
                                        "reason": "Empty workload name.",
                                        "capability": "inspect_workload",
                                        "target": "deployment/api",
                                        "arguments": {"name": ""},
                                        "expected_observations": ["healthy"],
                                        "message": None,
                                    }
                                ),
                            },
                        }
                    ]
                }
            )
        ),
    )

    with pytest.raises(JsonHttpModelError, match="argument name length must be >= 1"):
        await adapter.decide(context())


def test_openai_chat_completions_model_adapter_validates_configuration() -> None:
    with pytest.raises(ValueError, match="model name"):
        OpenAIChatCompletionsModelAdapter("", api_key="secret")
    with pytest.raises(ValueError, match="API key"):
        OpenAIChatCompletionsModelAdapter("gpt-runtime", api_key=" ")
    with pytest.raises(ValueError, match="endpoint"):
        OpenAIChatCompletionsModelAdapter("gpt-runtime", api_key="secret", endpoint="")
    with pytest.raises(ValueError, match="headers"):
        OpenAIChatCompletionsModelAdapter(
            "gpt-runtime",
            api_key="secret",
            extra_headers={"X-Test": "bad\n"},
        )


@pytest.mark.asyncio
async def test_openai_responses_model_adapter_posts_structured_output_request() -> None:
    transport = RecordingTransport(
        immutable_json(
            {
                "status": "completed",
                "output_text": json_text(
                    {
                        "type": "execute",
                        "reason": "Need current workload health.",
                        "capability": "inspect_workload",
                        "target": "deployment/api",
                        "arguments": {"name": "api", "namespace": "prod"},
                        "expected_observations": ["healthy", "ready_replicas"],
                        "message": None,
                    }
                ),
                "usage": {
                    "input_tokens": 120,
                    "output_tokens": 30,
                },
            }
        )
    )
    adapter = OpenAIResponsesModelAdapter(
        "gpt-runtime",
        api_key="openai-secret",
        endpoint="https://api.openai.example.test/v1/responses",
        extra_headers={"OpenAI-Organization": "org-test"},
        timeout_seconds=5.0,
        transport=transport,
    )

    decision = await adapter.decide(context())

    assert decision.type is DecisionType.EXECUTE
    assert decision.capability == "inspect_workload"
    assert decision.arguments == {"name": "api", "namespace": "prod"}
    assert model_usage(adapter) == ModelUsage(
        "openai_responses",
        "gpt-runtime",
        input_tokens=120,
        output_tokens=30,
    )
    request = transport.requests[0]
    assert request.url == "https://api.openai.example.test/v1/responses"
    assert request.headers["Authorization"] == "Bearer openai-secret"
    assert request.headers["OpenAI-Organization"] == "org-test"
    assert request.payload["model"] == "gpt-runtime"
    assert request.payload["store"] is False
    text = cast(Mapping[str, JsonValue], request.payload["text"])
    text_format = cast(Mapping[str, JsonValue], text["format"])
    assert text_format["type"] == "json_schema"
    assert text_format["strict"] is True
    schema = cast(Mapping[str, JsonValue], text_format["schema"])
    assert schema["additionalProperties"] is False
    input_items = cast(list[JsonValue], request.payload["input"])
    first_input = cast(Mapping[str, JsonValue], input_items[0])
    content = cast(list[JsonValue], first_input["content"])
    input_text = cast(Mapping[str, JsonValue], content[0])
    assert input_text["type"] == "input_text"
    prompt_text = cast(str, input_text["text"])
    assert "Verify workload health" in prompt_text
    assert "required_arguments" in prompt_text
    assert "argument_schema" in prompt_text
    assert "goal_success_criteria" in prompt_text


@pytest.mark.asyncio
async def test_openai_responses_model_adapter_reads_output_array_text() -> None:
    adapter = OpenAIResponsesModelAdapter(
        "gpt-runtime",
        api_key="openai-secret",
        transport=RecordingTransport(
            immutable_json(
                {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json_text(
                                        {
                                            "type": "finish",
                                            "reason": "All criteria satisfied.",
                                            "capability": None,
                                            "target": None,
                                            "arguments": {},
                                            "expected_observations": [],
                                            "message": None,
                                        }
                                    ),
                                }
                            ],
                        }
                    ],
                }
            )
        ),
    )

    decision = await adapter.decide(context())

    assert decision.type is DecisionType.FINISH
    assert decision.reason == "All criteria satisfied."


@pytest.mark.asyncio
async def test_openai_responses_model_adapter_rejects_incomplete_response() -> None:
    adapter = OpenAIResponsesModelAdapter(
        "gpt-runtime",
        api_key="openai-secret",
        transport=RecordingTransport(
            immutable_json(
                {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                }
            )
        ),
    )

    with pytest.raises(JsonHttpModelError, match="did not complete"):
        await adapter.decide(context())


@pytest.mark.asyncio
async def test_openai_responses_model_adapter_rejects_context_argument_contract_violation() -> None:
    adapter = OpenAIResponsesModelAdapter(
        "gpt-runtime",
        api_key="openai-secret",
        transport=RecordingTransport(
            immutable_json(
                {
                    "status": "completed",
                    "output_text": json_text(
                        {
                            "type": "execute",
                            "reason": "Empty workload name.",
                            "capability": "inspect_workload",
                            "target": "deployment/api",
                            "arguments": {"name": ""},
                            "expected_observations": ["healthy"],
                            "message": None,
                        }
                    ),
                }
            )
        ),
    )

    with pytest.raises(JsonHttpModelError, match="argument name length must be >= 1"):
        await adapter.decide(context())


def test_openai_responses_model_adapter_validates_configuration() -> None:
    with pytest.raises(ValueError, match="model name"):
        OpenAIResponsesModelAdapter("", api_key="secret")
    with pytest.raises(ValueError, match="API key"):
        OpenAIResponsesModelAdapter("gpt-runtime", api_key=" ")
    with pytest.raises(ValueError, match="endpoint"):
        OpenAIResponsesModelAdapter("gpt-runtime", api_key="secret", endpoint="")
    with pytest.raises(ValueError, match="headers"):
        OpenAIResponsesModelAdapter(
            "gpt-runtime",
            api_key="secret",
            extra_headers={"X-Test": "bad\n"},
        )


def json_text(payload: Mapping[str, object]) -> str:
    import json

    return json.dumps(payload, sort_keys=True)
