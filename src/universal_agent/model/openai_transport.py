from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, cast, runtime_checkable

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, OpenAIError

from universal_agent.core import JsonMapping, immutable_json
from universal_agent.core.config_validation import parse_json_object
from universal_agent.model.errors import JsonHttpModelError


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


@runtime_checkable
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


def _json_mapping(value: object, field_name: str) -> JsonMapping:
    candidate = dict(value) if isinstance(value, Mapping) else value
    try:
        return immutable_json(parse_json_object(candidate, field_name))
    except ValueError as exc:
        raise JsonHttpModelError(str(exc)) from exc
