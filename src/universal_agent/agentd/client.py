from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Any, cast
from urllib.parse import quote

import httpx

from universal_agent.core import (
    JsonCodecError,
    JsonMapping,
    JsonValue,
    dumps_json,
    immutable_json,
    loads_json,
)
from universal_agent.core.config_validation import (
    parse_json_object,
    parse_non_empty_string,
    parse_positive_float,
)


class AgentdClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True, slots=True)
class AgentdClientResponse:
    status_code: int
    body: JsonMapping


@dataclass(frozen=True, slots=True)
class AgentdTextResponse:
    status_code: int
    text: str
    content_type: str | None = None


class AgentdClient:
    """HTTP client adapter for the agentd Runtime API.

    The client owns wire concerns only: URL normalization, auth headers,
    request/response JSON and HTTP error mapping. Runtime state and behavior stay
    behind agentd's existing Runtime API routes.
    """

    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str | None = None,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = _base_url(base_url)
        self._bearer_token = _bearer_token(bearer_token)
        parse_positive_float(timeout_seconds, "agentd client timeout_seconds")
        self._timeout_seconds = timeout_seconds
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def __aenter__(self) -> AgentdClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_json(
        self,
        path: str,
        *,
        query: Mapping[str, object] | None = None,
    ) -> JsonMapping:
        response = await self._request("GET", path, query=query)
        return _response_json(response)

    async def post_json(
        self,
        path: str,
        *,
        body: Mapping[str, JsonValue] | None = None,
        query: Mapping[str, object] | None = None,
    ) -> JsonMapping:
        response = await self._request("POST", path, body=body, query=query)
        return _response_json(response)

    async def get_text(
        self,
        path: str,
        *,
        query: Mapping[str, object] | None = None,
    ) -> AgentdTextResponse:
        response = await self._request("GET", path, query=query)
        if response.status_code >= 400:
            _raise_http_error(response)
        return AgentdTextResponse(
            response.status_code,
            response.text,
            response.headers.get("content-type"),
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, JsonValue] | None = None,
        query: Mapping[str, object] | None = None,
    ) -> httpx.Response:
        headers = self._headers(body is not None)
        try:
            return await self._client.request(
                method,
                _request_url(self._base_url, path, query),
                headers=headers,
                content=None if body is None else dumps_json(dict(body)).encode("utf-8"),
                timeout=self._timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise AgentdClientError(f"agentd request failed: {exc}") from exc

    def _headers(self, has_body: bool) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if has_body:
            headers["Content-Type"] = "application/json"
        if self._bearer_token is not None:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        return headers


def quote_path_segment(value: str) -> str:
    return quote(value, safe="")


def _base_url(value: str) -> str:
    raw = parse_non_empty_string(value, "agentd api url")
    url = httpx.URL(raw)
    if url.scheme not in {"http", "https"} or url.host is None:
        raise ValueError("agentd api url must be an absolute http(s) URL")
    return str(url).rstrip("/")


def _bearer_token(value: str | None) -> str | None:
    if value is None:
        return None
    return parse_non_empty_string(value, "agentd api token")


def _request_url(
    base_url: str,
    path: str,
    query: Mapping[str, object] | None,
) -> str:
    normalized_path = "/" + path.lstrip("/")
    url = httpx.URL(f"{base_url}{normalized_path}")
    params = httpx.QueryParams(
        cast(Any, tuple((key, value) for key, value in (query or {}).items() if value is not None))
    )
    if not params:
        return str(url)
    return str(url.copy_merge_params(params))


def _response_json(response: httpx.Response) -> JsonMapping:
    if response.status_code >= 400:
        _raise_http_error(response)
    try:
        loaded = loads_json(response.content)
    except JsonCodecError as exc:
        raise AgentdClientError(
            f"agentd returned invalid JSON with HTTP {response.status_code}"
        ) from exc
    try:
        return immutable_json(parse_json_object(loaded, "agentd response"))
    except ValueError as exc:
        raise AgentdClientError(str(exc), status_code=response.status_code) from exc


def _raise_http_error(response: httpx.Response) -> None:
    code = None
    message = response.text.strip()
    try:
        loaded = loads_json(response.content)
    except JsonCodecError:
        loaded = None
    if isinstance(loaded, Mapping):
        error = loaded.get("error")
        if isinstance(error, Mapping):
            raw_code = error.get("code")
            raw_message = error.get("message")
            code = raw_code if isinstance(raw_code, str) and raw_code else None
            if isinstance(raw_message, str) and raw_message:
                message = raw_message
    if not message:
        message = response.reason_phrase
    prefix = f"agentd returned HTTP {response.status_code}"
    if code is not None:
        prefix += f" {code}"
    raise AgentdClientError(
        f"{prefix}: {message}",
        status_code=response.status_code,
        code=code,
    )
