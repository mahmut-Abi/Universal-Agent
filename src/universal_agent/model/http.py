from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, cast

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, OpenAIError
from pydantic import Field

from universal_agent.core import (
    CapabilitySummary,
    ContextFragment,
    Decision,
    DecisionContext,
    DecisionType,
    JsonCodecError,
    JsonMapping,
    JsonValue,
    Observation,
    SuccessCriterion,
    dumps_json,
    immutable_json,
    loads_json,
    validate_argument_contract,
)
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    parse_json_object,
    parse_non_empty_string,
    parse_non_empty_string_sequence,
    parse_optional_non_empty_string,
    parse_payload,
    parse_positive_float,
)
from universal_agent.model.adapter import ModelUsage


class JsonHttpModelError(RuntimeError):
    pass


class _DecisionPayload(ConfigPayload):
    type: str
    reason: str
    capability: str | None = None
    target: str | None = None
    arguments: dict[str, PydanticJsonValue] = Field(default_factory=dict)
    expected_observations: list[str] = Field(default_factory=list)
    message: str | None = None


class _UsagePayload(ConfigPayload):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_micros: int = Field(default=0, ge=0)
    currency: str | None = None


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


_MODEL_PAYLOAD_EXPECTED_TYPES = {"greater_than_equal": "a non-negative integer"}


class JsonHttpModelTransport(Protocol):
    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping: ...


class OpenAIClientResponse(Protocol):
    def model_dump(self, *, mode: str) -> object: ...


class OpenAIResponsesResource(Protocol):
    async def create(self, **kwargs: Any) -> OpenAIClientResponse: ...


class OpenAIChatCompletionsResource(Protocol):
    async def create(self, **kwargs: Any) -> OpenAIClientResponse: ...


class OpenAIChatResource(Protocol):
    completions: OpenAIChatCompletionsResource


class OpenAIClient(Protocol):
    responses: OpenAIResponsesResource
    chat: OpenAIChatResource

    async def close(self) -> None: ...


class OpenAIClientFactory(Protocol):
    def __call__(
        self,
        *,
        api_key: str,
        base_url: str,
        default_headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> OpenAIClient: ...


class OpenAIModelTransport(Protocol):
    async def create_response(
        self,
        endpoint: str,
        *,
        api_key: str,
        extra_headers: Mapping[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping: ...

    async def create_chat_completion(
        self,
        endpoint: str,
        *,
        api_key: str,
        extra_headers: Mapping[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping: ...


class OpenAISdkModelTransport:
    """OpenAI SDK-backed transport for OpenAI and OpenAI-compatible providers."""

    def __init__(self, client_factory: OpenAIClientFactory | None = None) -> None:
        self._client_factory = client_factory or _openai_client

    async def create_response(
        self,
        endpoint: str,
        *,
        api_key: str,
        extra_headers: Mapping[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping:
        client = self._client(
            endpoint,
            "/responses",
            api_key=api_key,
            extra_headers=extra_headers,
            timeout_seconds=timeout_seconds,
        )
        try:
            response = await client.responses.create(**_openai_kwargs(payload))
        except APIStatusError as exc:
            raise JsonHttpModelError(_openai_status_error_message(exc)) from exc
        except APITimeoutError as exc:
            raise JsonHttpModelError(f"OpenAI provider request timed out: {exc}") from exc
        except APIConnectionError as exc:
            raise JsonHttpModelError(f"OpenAI provider connection failed: {exc}") from exc
        except OpenAIError as exc:
            raise JsonHttpModelError(f"OpenAI provider request failed: {exc}") from exc
        finally:
            await client.close()
        return _openai_response_mapping(response, "OpenAI response")

    async def create_chat_completion(
        self,
        endpoint: str,
        *,
        api_key: str,
        extra_headers: Mapping[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping:
        client = self._client(
            endpoint,
            "/chat/completions",
            api_key=api_key,
            extra_headers=extra_headers,
            timeout_seconds=timeout_seconds,
        )
        try:
            response = await client.chat.completions.create(**_openai_kwargs(payload))
        except APIStatusError as exc:
            raise JsonHttpModelError(_openai_status_error_message(exc)) from exc
        except APITimeoutError as exc:
            raise JsonHttpModelError(f"OpenAI provider request timed out: {exc}") from exc
        except APIConnectionError as exc:
            raise JsonHttpModelError(f"OpenAI provider connection failed: {exc}") from exc
        except OpenAIError as exc:
            raise JsonHttpModelError(f"OpenAI provider request failed: {exc}") from exc
        finally:
            await client.close()
        return _openai_response_mapping(response, "OpenAI chat completion response")

    def _client(
        self,
        endpoint: str,
        resource_path: str,
        *,
        api_key: str,
        extra_headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> OpenAIClient:
        return self._client_factory(
            api_key=api_key,
            base_url=_openai_base_url(endpoint, resource_path),
            default_headers=extra_headers,
            timeout_seconds=timeout_seconds,
        )


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


class HttpxJsonHttpTransport:
    """Async HTTP transport for JSON model providers."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping:
        if self._client is not None:
            response = await self._post_with_client(
                self._client,
                url,
                headers=headers,
                payload=payload,
                timeout_seconds=timeout_seconds,
            )
        else:
            async with httpx.AsyncClient() as client:
                response = await self._post_with_client(
                    client,
                    url,
                    headers=headers,
                    payload=payload,
                    timeout_seconds=timeout_seconds,
                )
        try:
            decoded = loads_json(response.content)
        except JsonCodecError as exc:
            raise JsonHttpModelError(
                f"model provider returned invalid JSON: {_json_error_message(exc)}"
            ) from exc
        return _json_mapping(decoded, "response")

    async def _post_with_client(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> httpx.Response:
        try:
            response = await client.post(
                url,
                headers=dict(headers),
                content=dumps_json(payload).encode("utf-8"),
                timeout=timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()
            suffix = f": {detail}" if detail else ""
            raise JsonHttpModelError(
                f"model provider returned HTTP {exc.response.status_code}{suffix}"
            ) from exc
        except httpx.RequestError as exc:
            raise JsonHttpModelError(f"model provider request failed: {exc}") from exc
        return response


class StdlibJsonHttpTransport(HttpxJsonHttpTransport):
    """Backward-compatible name for the default async JSON HTTP transport."""


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
        parsed_endpoint = parse_non_empty_string(endpoint, "model endpoint")
        parsed_model = parse_non_empty_string(model, "model name")
        parsed_provider = parse_non_empty_string(provider, "model provider")
        parse_positive_float(timeout_seconds, "model timeout_seconds")
        _validate_headers(extra_headers or {})
        self._endpoint = parsed_endpoint
        self._model = parsed_model
        self._provider = parsed_provider
        self._api_key = api_key
        self._extra_headers = dict(extra_headers or {})
        self._timeout_seconds = timeout_seconds
        self._transport = transport or HttpxJsonHttpTransport()
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
        transport: OpenAIModelTransport | JsonHttpModelTransport | None = None,
    ) -> None:
        parsed_model = parse_non_empty_string(model, "model name")
        parsed_api_key = parse_non_empty_string(api_key, "OpenAI API key")
        parsed_endpoint = parse_non_empty_string(endpoint, "OpenAI responses endpoint")
        parse_positive_float(timeout_seconds, "model timeout_seconds")
        _validate_headers(extra_headers or {})
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
                f"OpenAI response output_text was not JSON: {_json_error_message(exc)}"
            ) from exc
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
        _validate_headers(extra_headers or {})
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
                    "content": dumps_json(prompt),
                },
            ],
        }
        response_format = _openai_chat_response_format(self._response_format)
        if response_format is not None:
            payload["response_format"] = response_format
        return immutable_json(payload)


def _openai_model_transport(
    transport: OpenAIModelTransport | JsonHttpModelTransport | None,
) -> OpenAIModelTransport:
    if transport is None:
        return OpenAISdkModelTransport()
    if hasattr(transport, "create_response") and hasattr(transport, "create_chat_completion"):
        return cast(OpenAIModelTransport, transport)
    return _LegacyOpenAIJsonHttpTransport(transport)


def _openai_client(
    *,
    api_key: str,
    base_url: str,
    default_headers: Mapping[str, str],
    timeout_seconds: float,
) -> OpenAIClient:
    return cast(
        OpenAIClient,
        AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=dict(default_headers),
            timeout=timeout_seconds,
        ),
    )


def _openai_kwargs(payload: JsonMapping) -> dict[str, Any]:
    return cast(dict[str, Any], dict(payload))


def _openai_headers(api_key: str, extra_headers: Mapping[str, str]) -> Mapping[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        **extra_headers,
    }


def _openai_base_url(endpoint: str, resource_path: str) -> str:
    url = httpx.URL(endpoint)
    if url.query or url.fragment:
        raise ValueError("OpenAI model endpoint must not include query or fragment")
    path = url.path.rstrip("/")
    normalized_resource_path = resource_path.rstrip("/")
    if path.endswith(normalized_resource_path):
        path = path[: -len(normalized_resource_path)].rstrip("/")
        return str(url.copy_with(path=path))
    return str(url.copy_with(path=path))


def _openai_response_mapping(response: object, field_name: str) -> JsonMapping:
    if isinstance(response, Mapping):
        return _json_mapping(response, field_name)
    model_dump = getattr(response, "model_dump", None)
    if not callable(model_dump):
        raise JsonHttpModelError(f"{field_name} was not an OpenAI SDK model")
    return _json_mapping(model_dump(mode="json"), field_name)


def _openai_status_error_message(error: APIStatusError) -> str:
    response = getattr(error, "response", None)
    detail = ""
    if response is not None:
        body = getattr(response, "text", "")
        if isinstance(body, str) and body.strip():
            detail = f": {body.strip()}"
    if not detail:
        message = str(error).strip()
        detail = f": {message}" if message else ""
    return f"OpenAI provider returned HTTP {error.status_code}{detail}"


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
    parsed = _parse_value_payload(_DecisionPayload, payload)
    decision_type = DecisionType(_model_non_empty_string(parsed.type, "type"))
    reason = _model_non_empty_string(parsed.reason, "reason")
    capability = _model_optional_non_empty_string(parsed.capability, "capability")
    target = _model_optional_non_empty_string(parsed.target, "target")
    arguments = immutable_json(parsed.arguments)
    expected_observations = parse_non_empty_string_sequence(
        parsed.expected_observations,
        "expected_observations",
        empty_template="{path} must be a non-empty string",
    )
    message = _model_optional_non_empty_string(parsed.message, "message")
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
    usage = _parse_model_payload(_UsagePayload, value, "usage")
    input_tokens = usage.input_tokens if usage.input_tokens is not None else usage.prompt_tokens
    output_tokens = (
        usage.output_tokens if usage.output_tokens is not None else usage.completion_tokens
    )
    currency = _model_optional_non_empty_string(usage.currency, "usage.currency") or "USD"
    return ModelUsage(
        provider,
        model,
        input_tokens=input_tokens or 0,
        output_tokens=output_tokens or 0,
        estimated_cost_micros=usage.estimated_cost_micros,
        currency=currency,
    )


def _openai_responses_payload(response: JsonMapping) -> _OpenAIResponsesPayload:
    return _parse_model_payload(_OpenAIResponsesPayload, response, "OpenAI response")


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
    return _parse_model_payload(
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
    message = f"{source} was not JSON: {_json_error_message(last_error)}"
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


def _json_error_message(error: JsonCodecError) -> str:
    return str(error).removeprefix("invalid JSON: ")


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


def _model_non_empty_string(value: str, field_name: str) -> str:
    return parse_non_empty_string(
        value,
        field_name,
        empty_template="{path} must be a non-empty string",
    )


def _model_optional_non_empty_string(value: str | None, field_name: str) -> str | None:
    return parse_optional_non_empty_string(
        value,
        field_name,
        empty_template="{path} must be a non-empty string",
    )


def _validate_headers(headers: Mapping[str, str]) -> None:
    for name, value in headers.items():
        parse_non_empty_string(name, "model extra header name")
        if "\n" in name or "\r" in name or "\n" in value or "\r" in value:
            raise ValueError("model extra headers must not contain newlines")


def _json_mapping(value: object, field_name: str) -> JsonMapping:
    candidate = dict(value) if isinstance(value, Mapping) else value
    try:
        return immutable_json(parse_json_object(candidate, field_name))
    except ValueError as exc:
        raise JsonHttpModelError(str(exc)) from exc


def _parse_value_payload[T: ConfigPayload](
    model_type: type[T],
    payload: Mapping[str, JsonValue],
) -> T:
    return parse_payload(
        model_type,
        payload,
        missing_template="{path} is required",
        expected_types=_MODEL_PAYLOAD_EXPECTED_TYPES,
    )


def _parse_model_payload[T: ConfigPayload](
    model_type: type[T],
    payload: Mapping[str, JsonValue],
    field_name: str,
) -> T:
    try:
        return parse_payload(
            model_type,
            payload,
            field=field_name,
            missing_template="{path} is required",
            expected_types=_MODEL_PAYLOAD_EXPECTED_TYPES,
        )
    except ValueError as exc:
        raise JsonHttpModelError(str(exc)) from exc
