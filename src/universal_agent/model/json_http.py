from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

import httpx

from universal_agent.core import (
    Decision,
    DecisionContext,
    JsonCodecError,
    JsonMapping,
    JsonValue,
    dumps_json,
    immutable_json,
    loads_json,
)
from universal_agent.core.config_validation import parse_non_empty_string, parse_positive_float
from universal_agent.model.adapter import ModelUsage
from universal_agent.model.decision_codec import (
    decision_context_payload,
    decision_payload,
    decode_decision,
    decode_usage,
    json_error_message,
    json_mapping,
    validate_decision_against_context,
    validate_headers,
)
from universal_agent.model.errors import JsonHttpModelError


class JsonHttpModelTransport(Protocol):
    async def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: JsonMapping,
        timeout_seconds: float,
    ) -> JsonMapping: ...


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
                f"model provider returned invalid JSON: {json_error_message(exc)}"
            ) from exc
        return json_mapping(decoded, "response")

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
        validate_headers(extra_headers or {})
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
        payload = decision_payload(response)
        try:
            decision = decode_decision(payload)
            decision.validate()
            validate_decision_against_context(decision, context)
        except ValueError as exc:
            raise JsonHttpModelError(f"invalid model decision: {exc}") from exc
        self._last_usage = decode_usage(self._provider, self._model, response.get("usage"))
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
            "context": dict(decision_context_payload(context)),
            "response_contract": response_contract,
        }
        return immutable_json(payload)
