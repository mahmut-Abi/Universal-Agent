from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from universal_agent.core import (
    Decision,
    DecisionContext,
    DecisionType,
    JsonCodecError,
    JsonMapping,
    JsonValue,
    dumps_json,
    immutable_json,
    loads_json,
)
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    parse_json_object,
    parse_non_empty_string,
    parse_positive_float,
)
from universal_agent.model.adapter import ModelUsage
from universal_agent.model.decision_codec import (
    decision_context_payload,
    decision_payload,
    decode_decision,
    decode_usage,
    json_error_message,
    json_mapping,
    parse_model_payload,
    validate_decision_against_context,
    validate_headers,
)
from universal_agent.model.errors import JsonHttpModelError
from universal_agent.model.json_http import JsonHttpModelTransport
from universal_agent.model.openai_transport import OpenAIModelTransport, OpenAISdkModelTransport

_SchemaNonEmptyString = Annotated[str, StringConstraints(min_length=1)]


class _OpenAIDecisionSchemaPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", title="universal_agent_decision")

    type: _SchemaNonEmptyString = Field(
        json_schema_extra={"enum": [item.value for item in DecisionType]}
    )
    reason: _SchemaNonEmptyString
    capability: str | None
    target: str | None
    arguments: dict[str, Any]
    expected_observations: list[_SchemaNonEmptyString]
    message: str | None


class _OpenAIResponsesContentPayload(ConfigPayload):
    type: str | None = None
    text: str | None = None


class _OpenAIResponsesOutputPayload(ConfigPayload):
    content: list[_OpenAIResponsesContentPayload] = Field(default_factory=list)


class _OpenAIResponsesPayload(ConfigPayload):
    status: str | None = None
    output_text: str | None = None
    output: list[_OpenAIResponsesOutputPayload] = Field(default_factory=list)
    error: PydanticJsonValue = None
    incomplete_details: PydanticJsonValue = None


class _OpenAIChatContentPartPayload(ConfigPayload):
    text: str | None = None


class _OpenAIChatMessagePayload(ConfigPayload):
    content: str | list[_OpenAIChatContentPartPayload] | None = None
    refusal: str | None = None


class _OpenAIChatChoicePayload(ConfigPayload):
    finish_reason: str | None = None
    message: _OpenAIChatMessagePayload | None = None


class _OpenAIChatCompletionPayload(ConfigPayload):
    choices: list[_OpenAIChatChoicePayload] = Field(default_factory=list)


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
        transport: OpenAIModelTransport | JsonHttpModelTransport | None = None,
    ) -> None:
        parsed_model = parse_non_empty_string(model, "model name")
        parsed_api_key = parse_non_empty_string(api_key, "OpenAI API key")
        parsed_endpoint = parse_non_empty_string(endpoint, "OpenAI responses endpoint")
        parse_positive_float(timeout_seconds, "model timeout_seconds")
        validate_headers(extra_headers or {})
        self._model = parsed_model
        self._api_key = parsed_api_key
        self._endpoint = parsed_endpoint
        self._extra_headers = dict(extra_headers or {})
        self._timeout_seconds = timeout_seconds
        self._transport = _openai_model_transport(transport)
        self._last_usage: ModelUsage | None = None

    async def decide(self, context: DecisionContext) -> Decision:
        response = await self._transport.create_response(
            self._endpoint,
            api_key=self._api_key,
            extra_headers=self._extra_headers,
            payload=self._request_payload(context),
            timeout_seconds=self._timeout_seconds,
        )
        openai_response = _openai_responses_payload(response)
        _raise_for_openai_response_status(openai_response)
        output_text = _openai_output_text(openai_response)
        try:
            decoded = loads_json(output_text)
        except JsonCodecError as exc:
            raise JsonHttpModelError(
                f"OpenAI response output_text was not JSON: {json_error_message(exc)}"
            ) from exc
        payload = decision_payload(json_mapping(decoded, "output_text"))
        try:
            decision = decode_decision(payload)
            decision.validate()
            validate_decision_against_context(decision, context)
        except ValueError as exc:
            raise JsonHttpModelError(f"invalid OpenAI model decision: {exc}") from exc
        self._last_usage = decode_usage(
            "openai_responses",
            self._model,
            response.get("usage"),
        )
        return decision

    def model_usage(self) -> ModelUsage | None:
        return self._last_usage

    def _request_payload(self, context: DecisionContext) -> JsonMapping:
        context_payload = decision_context_payload(context)
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
                            "text": dumps_json(prompt),
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
        transport: OpenAIModelTransport | JsonHttpModelTransport | None = None,
    ) -> None:
        parsed_model = parse_non_empty_string(model, "model name")
        parsed_api_key = parse_non_empty_string(api_key, "OpenAI API key")
        parsed_endpoint = parse_non_empty_string(endpoint, "OpenAI chat completions endpoint")
        parse_positive_float(timeout_seconds, "model timeout_seconds")
        if response_format not in {"json_schema", "json_object", "prompt_json"}:
            raise ValueError(
                "OpenAI chat completions response_format must be "
                "json_schema, json_object, or prompt_json"
            )
        validate_headers(extra_headers or {})
        self._model = parsed_model
        self._api_key = parsed_api_key
        self._endpoint = parsed_endpoint
        self._extra_headers = dict(extra_headers or {})
        self._timeout_seconds = timeout_seconds
        self._response_format = response_format
        self._transport = _openai_model_transport(transport)
        self._last_usage: ModelUsage | None = None

    async def decide(self, context: DecisionContext) -> Decision:
        response = await self._transport.create_chat_completion(
            self._endpoint,
            api_key=self._api_key,
            extra_headers=self._extra_headers,
            payload=self._request_payload(context),
            timeout_seconds=self._timeout_seconds,
        )
        output_text = _openai_chat_completion_content(_openai_chat_completion_payload(response))
        decoded = _loads_json_text(output_text, "OpenAI chat completion message content")
        payload = decision_payload(json_mapping(decoded, "message.content"))
        try:
            decision = decode_decision(payload)
            decision.validate()
            validate_decision_against_context(decision, context)
        except ValueError as exc:
            raise JsonHttpModelError(f"invalid OpenAI chat completion decision: {exc}") from exc
        self._last_usage = decode_usage(
            "openai_chat_completions",
            self._model,
            response.get("usage"),
        )
        return decision

    def model_usage(self) -> ModelUsage | None:
        return self._last_usage

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
            "context": dict(decision_context_payload(context)),
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
                    "content": dumps_json(prompt),
                },
            ],
        }
        response_format = _openai_chat_response_format(self._response_format)
        if response_format is not None:
            payload["response_format"] = response_format
        return immutable_json(payload)


class _LegacyOpenAIJsonHttpTransport:
    def __init__(self, transport: JsonHttpModelTransport) -> None:
        self._transport = transport

    async def create_response(
        self,
        endpoint: str,
        *,
        api_key: str,
        extra_headers: Mapping[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping:
        return await self._transport.post_json(
            endpoint,
            headers=_openai_headers(api_key, extra_headers),
            payload=payload,
            timeout_seconds=timeout_seconds,
        )

    async def create_chat_completion(
        self,
        endpoint: str,
        *,
        api_key: str,
        extra_headers: Mapping[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping:
        return await self._transport.post_json(
            endpoint,
            headers=_openai_headers(api_key, extra_headers),
            payload=payload,
            timeout_seconds=timeout_seconds,
        )


def _openai_model_transport(
    transport: OpenAIModelTransport | JsonHttpModelTransport | None,
) -> OpenAIModelTransport:
    if transport is None:
        return OpenAISdkModelTransport()
    if hasattr(transport, "create_response") and hasattr(transport, "create_chat_completion"):
        return cast(OpenAIModelTransport, transport)
    return _LegacyOpenAIJsonHttpTransport(transport)


def _openai_headers(api_key: str, extra_headers: Mapping[str, str]) -> Mapping[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        **extra_headers,
    }


def _openai_responses_payload(response: JsonMapping) -> _OpenAIResponsesPayload:
    return parse_model_payload(_OpenAIResponsesPayload, response, "OpenAI response")


def _raise_for_openai_response_status(response: _OpenAIResponsesPayload) -> None:
    status = response.status
    if status is None or status == "completed":
        return
    detail = response.error or response.incomplete_details
    suffix = f": {detail}" if detail is not None else ""
    raise JsonHttpModelError(f"OpenAI response did not complete: {status}{suffix}")


def _openai_output_text(response: _OpenAIResponsesPayload) -> str:
    if response.output_text is not None and response.output_text.strip():
        return response.output_text
    parts: list[str] = []
    for item in response.output:
        for content_item in item.content:
            if content_item.type != "output_text":
                continue
            if content_item.text is not None:
                parts.append(content_item.text)
    output_text = "".join(parts).strip()
    if not output_text:
        raise JsonHttpModelError("OpenAI response missing output_text")
    return output_text


def _openai_chat_completion_payload(response: JsonMapping) -> _OpenAIChatCompletionPayload:
    return parse_model_payload(
        _OpenAIChatCompletionPayload,
        response,
        "OpenAI chat completion response",
    )


def _openai_chat_completion_content(response: _OpenAIChatCompletionPayload) -> str:
    if not response.choices:
        raise JsonHttpModelError("OpenAI chat completion response missing choices")
    choice = response.choices[0]
    finish_reason = choice.finish_reason
    if finish_reason in {"length", "content_filter", "tool_calls", "function_call"}:
        raise JsonHttpModelError(
            f"OpenAI chat completion did not return final JSON content: {finish_reason}"
        )
    if choice.message is None:
        raise JsonHttpModelError("OpenAI chat completion choice missing message")
    message = choice.message
    if message.refusal is not None and message.refusal.strip():
        raise JsonHttpModelError("OpenAI chat completion refused the decision request")
    content = message.content
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts = [item.text for item in content if item.text is not None]
        joined = "".join(parts).strip()
        if joined:
            return joined
    raise JsonHttpModelError("OpenAI chat completion message missing content")


def _loads_json_text(text: str, source: str) -> object:
    candidates = (text, _strip_json_code_fence(text))
    last_error: JsonCodecError | None = None
    for candidate in dict.fromkeys(candidates):
        try:
            return loads_json(candidate)
        except JsonCodecError as exc:
            last_error = exc
    assert last_error is not None
    message = f"{source} was not JSON: {json_error_message(last_error)}"
    raise JsonHttpModelError(message) from last_error


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
    return dict(
        parse_json_object(
            _OpenAIDecisionSchemaPayload.model_json_schema(),
            "OpenAI decision JSON schema",
        )
    )
