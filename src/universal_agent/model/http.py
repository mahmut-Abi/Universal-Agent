from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from universal_agent.core import (
    CapabilitySummary,
    ContextFragment,
    Decision,
    DecisionContext,
    DecisionType,
    JsonMapping,
    JsonValue,
    Observation,
    SuccessCriterion,
    immutable_json,
    validate_argument_contract,
)
from universal_agent.model.adapter import ModelUsage


class JsonHttpModelError(RuntimeError):
    pass


class JsonHttpModelTransport(Protocol):
    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping: ...


class StdlibJsonHttpTransport:
    """Small stdlib HTTP transport for JSON model providers."""

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping:
        return await asyncio.to_thread(
            self._post_json_sync,
            url,
            headers,
            payload,
            timeout_seconds,
        )

    def _post_json_sync(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping:
        request = Request(
            url,
            data=json.dumps(payload, sort_keys=True).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            suffix = f": {detail}" if detail else ""
            raise JsonHttpModelError(f"model provider returned HTTP {exc.code}{suffix}") from exc
        except URLError as exc:
            raise JsonHttpModelError(f"model provider request failed: {exc.reason}") from exc
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError as exc:
            raise JsonHttpModelError(f"model provider returned invalid JSON: {exc}") from exc
        return _json_mapping(decoded, "response")


class JsonHttpModelAdapter:
    """Model adapter for providers that accept context JSON and return a Decision JSON.

    This adapter intentionally stays provider-agnostic. The Runtime contract is
    the structured `Decision`; deployments can place a small provider-specific
    bridge behind the HTTP endpoint without teaching the Kernel about any model
    vendor.
    """

    def __init__(
        self,
        endpoint: str,
        model: str,
        *,
        provider: str = "json-http",
        api_key: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 30.0,
        transport: JsonHttpModelTransport | None = None,
    ) -> None:
        if not endpoint.strip():
            raise ValueError("model endpoint must not be empty")
        if not model.strip():
            raise ValueError("model name must not be empty")
        if not provider.strip():
            raise ValueError("model provider must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("model timeout_seconds must be positive")
        _validate_headers(extra_headers or {})
        self._endpoint = endpoint
        self._model = model
        self._provider = provider
        self._api_key = api_key
        self._extra_headers = dict(extra_headers or {})
        self._timeout_seconds = timeout_seconds
        self._transport = transport or StdlibJsonHttpTransport()
        self._last_usage: ModelUsage | None = None

    async def decide(self, context: DecisionContext) -> Decision:
        response = await self._transport.post_json(
            self._endpoint,
            headers=self._headers(),
            payload=self._request_payload(context),
            timeout_seconds=self._timeout_seconds,
        )
        decision_payload = _decision_payload(response)
        try:
            decision = _decode_decision(decision_payload)
            decision.validate()
            _validate_decision_against_context(decision, context)
        except ValueError as exc:
            raise JsonHttpModelError(f"invalid model decision: {exc}") from exc
        self._last_usage = _decode_usage(self._provider, self._model, response.get("usage"))
        return decision

    def model_usage(self) -> ModelUsage | None:
        return self._last_usage

    def _headers(self) -> Mapping[str, str]:
        headers = {"Content-Type": "application/json", **self._extra_headers}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _request_payload(self, context: DecisionContext) -> JsonMapping:
        response_contract: dict[str, JsonValue] = {
            "decision": {
                "type": "execute|wait|ask_user|finish",
                "reason": "non-empty string",
                "capability": "required for execute",
                "target": "optional string",
                "arguments": "object",
                "expected_observations": "required non-empty string array for execute",
                "message": "required for ask_user",
            },
            "usage": {
                "input_tokens": "optional non-negative integer",
                "output_tokens": "optional non-negative integer",
                "estimated_cost_micros": "optional non-negative integer",
                "currency": "optional string",
            },
        }
        payload: dict[str, JsonValue] = {
            "model": self._model,
            "context": dict(_decision_context_payload(context)),
            "response_contract": response_contract,
        }
        return immutable_json(payload)


class OpenAIResponsesModelAdapter:
    """OpenAI Responses API adapter that returns runtime-owned Decisions.

    The adapter uses Responses structured output to ask the provider for a
    JSON Decision payload, then still validates the decoded Decision locally.
    It does not expose OpenAI tools to the model; Runtime capability selection,
    policy and Tool execution remain owned by the Universal Agent Runtime.
    """

    DEFAULT_ENDPOINT = "https://api.openai.com/v1/responses"

    def __init__(
        self,
        model: str,
        *,
        api_key: str,
        endpoint: str = DEFAULT_ENDPOINT,
        extra_headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 30.0,
        transport: JsonHttpModelTransport | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model name must not be empty")
        if not api_key.strip():
            raise ValueError("OpenAI API key must not be empty")
        if not endpoint.strip():
            raise ValueError("OpenAI responses endpoint must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("model timeout_seconds must be positive")
        _validate_headers(extra_headers or {})
        self._model = model
        self._api_key = api_key
        self._endpoint = endpoint
        self._extra_headers = dict(extra_headers or {})
        self._timeout_seconds = timeout_seconds
        self._transport = transport or StdlibJsonHttpTransport()
        self._last_usage: ModelUsage | None = None

    async def decide(self, context: DecisionContext) -> Decision:
        response = await self._transport.post_json(
            self._endpoint,
            headers=self._headers(),
            payload=self._request_payload(context),
            timeout_seconds=self._timeout_seconds,
        )
        _raise_for_openai_response_status(response)
        output_text = _openai_output_text(response)
        try:
            decoded = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise JsonHttpModelError(f"OpenAI response output_text was not JSON: {exc}") from exc
        decision_payload = _decision_payload(_json_mapping(decoded, "output_text"))
        try:
            decision = _decode_decision(decision_payload)
            decision.validate()
            _validate_decision_against_context(decision, context)
        except ValueError as exc:
            raise JsonHttpModelError(f"invalid OpenAI model decision: {exc}") from exc
        self._last_usage = _decode_usage(
            "openai_responses",
            self._model,
            response.get("usage"),
        )
        return decision

    def model_usage(self) -> ModelUsage | None:
        return self._last_usage

    def _headers(self) -> Mapping[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            **self._extra_headers,
        }

    def _request_payload(self, context: DecisionContext) -> JsonMapping:
        context_payload = _decision_context_payload(context)
        prompt = {
            "runtime_contract": (
                "Return exactly one Universal Agent Runtime Decision. "
                "Use execute only for one capability listed in context.capabilities. "
                "Construct execute arguments from that capability's required_arguments "
                "and argument_schema. "
                "Use expected_observations for the evidence claims the Runtime should observe. "
                "Use finish only when goal_success_criteria and current_task_required_criteria "
                "are already satisfied in the runtime context. "
                "Do not claim tool execution or task completion in prose."
            ),
            "context": dict(context_payload),
        }
        payload: dict[str, JsonValue] = {
            "model": self._model,
            "store": False,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(prompt, sort_keys=True, separators=(",", ":")),
                        }
                    ],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "universal_agent_decision",
                    "strict": True,
                    "schema": _openai_decision_json_schema(),
                }
            },
        }
        return immutable_json(payload)


class OpenAIChatCompletionsModelAdapter:
    """OpenAI Chat Completions adapter that returns runtime-owned Decisions.

    This adapter targets OpenAI-compatible `/v1/chat/completions` providers for
    deployments that have not moved to the Responses API. The model still only
    proposes a structured Decision; Runtime-owned validation, policy, tool
    execution and evaluation remain unchanged.
    """

    DEFAULT_ENDPOINT = "https://api.openai.com/v1/chat/completions"

    def __init__(
        self,
        model: str,
        *,
        api_key: str,
        endpoint: str = DEFAULT_ENDPOINT,
        extra_headers: Mapping[str, str] | None = None,
        timeout_seconds: float = 30.0,
        response_format: str = "json_schema",
        transport: JsonHttpModelTransport | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model name must not be empty")
        if not api_key.strip():
            raise ValueError("OpenAI API key must not be empty")
        if not endpoint.strip():
            raise ValueError("OpenAI chat completions endpoint must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("model timeout_seconds must be positive")
        if response_format not in {"json_schema", "json_object", "prompt_json"}:
            raise ValueError(
                "OpenAI chat completions response_format must be "
                "json_schema, json_object, or prompt_json"
            )
        _validate_headers(extra_headers or {})
        self._model = model
        self._api_key = api_key
        self._endpoint = endpoint
        self._extra_headers = dict(extra_headers or {})
        self._timeout_seconds = timeout_seconds
        self._response_format = response_format
        self._transport = transport or StdlibJsonHttpTransport()
        self._last_usage: ModelUsage | None = None

    async def decide(self, context: DecisionContext) -> Decision:
        response = await self._transport.post_json(
            self._endpoint,
            headers=self._headers(),
            payload=self._request_payload(context),
            timeout_seconds=self._timeout_seconds,
        )
        output_text = _openai_chat_completion_content(response)
        decoded = _loads_json_text(output_text, "OpenAI chat completion message content")
        decision_payload = _decision_payload(_json_mapping(decoded, "message.content"))
        try:
            decision = _decode_decision(decision_payload)
            decision.validate()
            _validate_decision_against_context(decision, context)
        except ValueError as exc:
            raise JsonHttpModelError(f"invalid OpenAI chat completion decision: {exc}") from exc
        self._last_usage = _decode_usage(
            "openai_chat_completions",
            self._model,
            response.get("usage"),
        )
        return decision

    def model_usage(self) -> ModelUsage | None:
        return self._last_usage

    def _headers(self) -> Mapping[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            **self._extra_headers,
        }

    def _request_payload(self, context: DecisionContext) -> JsonMapping:
        prompt = {
            "runtime_contract": (
                "Return exactly one Universal Agent Runtime Decision as JSON. "
                "Use execute only for one capability listed in context.capabilities. "
                "Construct execute arguments from that capability's required_arguments "
                "and argument_schema. "
                "Use expected_observations for the evidence claims the Runtime should observe. "
                "Use finish only when goal_success_criteria and current_task_required_criteria "
                "are already satisfied in the runtime context. "
                "Do not claim tool execution or task completion in prose."
            ),
            "context": dict(_decision_context_payload(context)),
        }
        payload: dict[str, JsonValue] = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a decision component inside Universal Agent Runtime. "
                        "Return only valid JSON matching the requested schema. "
                        "Do not call tools, invent unavailable capabilities, or decide "
                        "that runtime state has changed."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt, sort_keys=True, separators=(",", ":")),
                },
            ],
        }
        response_format = _openai_chat_response_format(self._response_format)
        if response_format is not None:
            payload["response_format"] = response_format
        return immutable_json(payload)


def _decision_context_payload(context: DecisionContext) -> JsonMapping:
    payload: dict[str, JsonValue] = {
        "session_id": str(context.session_id),
        "goal_id": str(context.goal_id),
        "goal_description": context.goal_description,
        "goal_success_criteria": [
            _success_criterion_payload(item) for item in context.goal_success_criteria
        ],
        "task_id": str(context.task_id),
        "task_description": context.task_description,
        "current_task_required_criteria": list(context.current_task_required_criteria),
        "iteration": context.iteration,
        "satisfied_criteria": dict(context.satisfied_criteria),
        "latest_observation": _observation_payload(context.latest_observation),
        "capabilities": [_capability_payload(item) for item in context.capabilities],
        "domain_context": _fragment_payloads(context.domain_context),
        "world_context": _fragment_payloads(context.world_context),
        "evidence_context": _fragment_payloads(context.evidence_context),
        "task_context": _fragment_payloads(context.task_context),
        "memory_context": _fragment_payloads(context.memory_context),
        "policy_summary": list(context.policy_summary),
    }
    return immutable_json(payload)


def _success_criterion_payload(criterion: SuccessCriterion) -> dict[str, JsonValue]:
    return {"key": criterion.key, "expected": criterion.expected}


def _observation_payload(observation: Observation | None) -> dict[str, JsonValue] | None:
    if observation is None:
        return None
    return {
        "observation_id": str(observation.id),
        "action_id": str(observation.action_id),
        "task_id": str(observation.task_id),
        "source": observation.source,
        "status": observation.status.value,
        "data": dict(observation.data),
        "observed_at": observation.observed_at.isoformat(),
        "error": observation.error,
        "error_code": None if observation.error_code is None else observation.error_code.value,
    }


def _capability_payload(capability: CapabilitySummary) -> dict[str, JsonValue]:
    return {
        "name": capability.name,
        "description": capability.description,
        "category": capability.category.value,
        "risk": capability.risk.value,
        "required_arguments": list(capability.required_arguments),
        "argument_schema": dict(capability.argument_schema),
    }


def _fragment_payloads(fragments: tuple[ContextFragment, ...]) -> list[JsonValue]:
    return [
        {"key": item.key, "content": item.content, "priority": item.priority} for item in fragments
    ]


def _decision_payload(response: JsonMapping) -> JsonMapping:
    raw_decision = response.get("decision", response)
    if not isinstance(raw_decision, Mapping):
        raise JsonHttpModelError("model response decision must be an object")
    return _json_mapping(raw_decision, "decision")


def _decode_decision(payload: JsonMapping) -> Decision:
    decision_type = DecisionType(_required_string(payload, "type"))
    reason = _required_string(payload, "reason")
    capability = _optional_string(payload.get("capability"), "capability")
    target = _optional_string(payload.get("target"), "target")
    arguments = _optional_mapping(payload.get("arguments"), "arguments")
    expected_observations = _optional_string_tuple(
        payload.get("expected_observations"),
        "expected_observations",
    )
    message = _optional_string(payload.get("message"), "message")
    return Decision(
        decision_type,
        reason,
        capability=capability,
        target=target,
        arguments=arguments,
        expected_observations=expected_observations,
        message=message,
    )


def _validate_decision_against_context(decision: Decision, context: DecisionContext) -> None:
    if decision.type is not DecisionType.EXECUTE:
        return
    capability = decision.capability or ""
    available = {item.name: item for item in context.capabilities}
    summary = available.get(capability)
    if summary is None:
        raise ValueError(f"capability is not available in context: {capability}")
    argument_error = validate_argument_contract(
        required_arguments=summary.required_arguments,
        argument_schema=summary.argument_schema,
        arguments=decision.arguments,
    )
    if argument_error is not None:
        raise ValueError(f"arguments for capability {capability}: {argument_error}")


def _decode_usage(provider: str, model: str, value: JsonValue) -> ModelUsage | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise JsonHttpModelError("model usage must be an object")
    usage = _json_mapping(value, "usage")
    input_tokens = _optional_non_negative_int(
        usage.get("input_tokens", usage.get("prompt_tokens", 0)),
        "usage.input_tokens",
    )
    output_tokens = _optional_non_negative_int(
        usage.get("output_tokens", usage.get("completion_tokens", 0)),
        "usage.output_tokens",
    )
    estimated_cost_micros = _optional_non_negative_int(
        usage.get("estimated_cost_micros", 0),
        "usage.estimated_cost_micros",
    )
    currency = _optional_string(usage.get("currency"), "usage.currency") or "USD"
    return ModelUsage(
        provider,
        model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_micros=estimated_cost_micros,
        currency=currency,
    )


def _raise_for_openai_response_status(response: JsonMapping) -> None:
    status = response.get("status")
    if status is None or status == "completed":
        return
    if not isinstance(status, str):
        raise JsonHttpModelError("OpenAI response status must be a string")
    detail = response.get("error") or response.get("incomplete_details")
    suffix = f": {detail}" if detail is not None else ""
    raise JsonHttpModelError(f"OpenAI response did not complete: {status}{suffix}")


def _openai_output_text(response: JsonMapping) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    output = response.get("output")
    if not isinstance(output, list):
        raise JsonHttpModelError("OpenAI response missing output_text")
    parts: list[str] = []
    for item in output:
        if not isinstance(item, Mapping):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for content_item in content:
            if not isinstance(content_item, Mapping):
                continue
            if content_item.get("type") != "output_text":
                continue
            text = content_item.get("text")
            if isinstance(text, str):
                parts.append(text)
    output_text = "".join(parts).strip()
    if not output_text:
        raise JsonHttpModelError("OpenAI response missing output_text")
    return output_text


def _openai_chat_completion_content(response: JsonMapping) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise JsonHttpModelError("OpenAI chat completion response missing choices")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise JsonHttpModelError("OpenAI chat completion choice must be an object")
    choice = _json_mapping(first, "choices[0]")
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise JsonHttpModelError("OpenAI chat completion finish_reason must be a string")
    if finish_reason in {"length", "content_filter", "tool_calls", "function_call"}:
        raise JsonHttpModelError(
            f"OpenAI chat completion did not return final JSON content: {finish_reason}"
        )
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise JsonHttpModelError("OpenAI chat completion choice missing message")
    message_payload = _json_mapping(message, "choices[0].message")
    refusal = message_payload.get("refusal")
    if isinstance(refusal, str) and refusal.strip():
        raise JsonHttpModelError("OpenAI chat completion refused the decision request")
    content = message_payload.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, Mapping):
                continue
            content_item = _json_mapping(item, "choices[0].message.content[]")
            text = content_item.get("text")
            if isinstance(text, str):
                parts.append(text)
        joined = "".join(parts).strip()
        if joined:
            return joined
    raise JsonHttpModelError("OpenAI chat completion message missing content")


def _loads_json_text(text: str, source: str) -> object:
    candidates = (text, _strip_json_code_fence(text))
    last_error: json.JSONDecodeError | None = None
    for candidate in dict.fromkeys(candidates):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
    assert last_error is not None
    raise JsonHttpModelError(f"{source} was not JSON: {last_error}") from last_error


def _strip_json_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return text
    opening = lines[0].strip().lower()
    if opening not in {"```", "```json"}:
        return text
    return "\n".join(lines[1:-1]).strip()


def _openai_chat_response_format(mode: str) -> dict[str, JsonValue] | None:
    if mode == "prompt_json":
        return None
    if mode == "json_object":
        return {"type": "json_object"}
    if mode == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "universal_agent_decision",
                "strict": True,
                "schema": _openai_decision_json_schema(),
            },
        }
    raise ValueError(
        "OpenAI chat completions response_format must be json_schema, json_object, or prompt_json"
    )


def _openai_decision_json_schema() -> dict[str, JsonValue]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "type",
            "reason",
            "capability",
            "target",
            "arguments",
            "expected_observations",
            "message",
        ],
        "properties": {
            "type": {"type": "string", "enum": [item.value for item in DecisionType]},
            "reason": {"type": "string", "minLength": 1},
            "capability": {"type": ["string", "null"]},
            "target": {"type": ["string", "null"]},
            "arguments": {"type": "object", "additionalProperties": True},
            "expected_observations": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "message": {"type": ["string", "null"]},
        },
    }


def _required_string(payload: JsonMapping, field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _optional_string(value: JsonValue, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _optional_mapping(value: JsonValue, field_name: str) -> JsonMapping:
    if value is None:
        return immutable_json()
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return _json_mapping(value, field_name)


def _optional_string_tuple(value: JsonValue, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name}[{index}] must be a non-empty string")
        result.append(item)
    return tuple(result)


def _optional_non_negative_int(value: JsonValue, field_name: str) -> int:
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise JsonHttpModelError(f"{field_name} must be a non-negative integer")
    return value


def _validate_headers(headers: Mapping[str, str]) -> None:
    for name, value in headers.items():
        if not name.strip():
            raise ValueError("model extra header name must not be empty")
        if "\n" in name or "\r" in name or "\n" in value or "\r" in value:
            raise ValueError("model extra headers must not contain newlines")


def _json_mapping(value: object, field_name: str) -> JsonMapping:
    if not isinstance(value, Mapping):
        raise JsonHttpModelError(f"{field_name} must be an object")
    result: dict[str, JsonValue] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise JsonHttpModelError(f"{field_name} keys must be strings")
        result[key] = _json_value(item, f"{field_name}.{key}")
    return immutable_json(result)


def _json_value(value: object, field_name: str) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list):
        return [_json_value(item, f"{field_name}[]") for item in value]
    if isinstance(value, Mapping):
        return dict(_json_mapping(value, field_name))
    raise JsonHttpModelError(f"{field_name} must be JSON-compatible")
