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
    immutable_json,
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


def _decision_context_payload(context: DecisionContext) -> JsonMapping:
    payload: dict[str, JsonValue] = {
        "session_id": str(context.session_id),
        "goal_id": str(context.goal_id),
        "goal_description": context.goal_description,
        "task_id": str(context.task_id),
        "task_description": context.task_description,
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
