from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from universal_agent.core import JsonMapping, JsonValue, immutable_json
from universal_agent.core.config_validation import (
    parse_json_object,
    parse_non_empty_string,
    parse_optional_non_empty_string,
    parse_positive_float,
)


class PrometheusQueryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PrometheusResponse:
    status_code: int
    payload: JsonValue = None
    text: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)


class PrometheusTransport(Protocol):
    async def request(
        self,
        path: str,
        *,
        query: Mapping[str, str],
        timeout_seconds: float | None = None,
    ) -> PrometheusResponse: ...


class HttpxPrometheusTransport:
    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = _base_url(base_url)
        self._client = client

    async def request(
        self,
        path: str,
        *,
        query: Mapping[str, str],
        timeout_seconds: float | None = None,
    ) -> PrometheusResponse:
        if self._client is not None:
            return await self._request_with_client(
                self._client,
                path,
                query=query,
                timeout_seconds=timeout_seconds,
            )
        async with httpx.AsyncClient() as client:
            return await self._request_with_client(
                client,
                path,
                query=query,
                timeout_seconds=timeout_seconds,
            )

    async def _request_with_client(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        query: Mapping[str, str],
        timeout_seconds: float | None,
    ) -> PrometheusResponse:
        try:
            response = await client.get(
                str(self._base_url.copy_with(path=_joined_path(self._base_url.path, path))),
                params=query,
                timeout=timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise PrometheusQueryError(f"Prometheus request failed: {exc}") from exc
        payload: JsonValue
        try:
            loaded = response.json()
        except ValueError:
            payload = None
        else:
            payload = loaded
        return PrometheusResponse(
            response.status_code,
            payload,
            response.text,
            dict(response.headers.items()),
        )


class PrometheusBackend:
    """Metrics backend for Prometheus instant queries."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: PrometheusTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        parse_positive_float(timeout_seconds, "timeout_seconds")
        self._transport = transport or HttpxPrometheusTransport(base_url)
        self._timeout_seconds = timeout_seconds

    async def query(self, arguments: JsonMapping) -> JsonMapping:
        query = parse_non_empty_string(arguments.get("query"), "query")
        subject = parse_optional_non_empty_string(arguments.get("subject"), "subject")
        response = await self._transport.request(
            "/api/v1/query",
            query={"query": query},
            timeout_seconds=self._timeout_seconds,
        )
        if response.status_code < 200 or response.status_code >= 300:
            message = response.text.strip() or f"HTTP {response.status_code}"
            raise PrometheusQueryError(f"Prometheus query failed: {message}")
        payload = parse_json_object(response.payload, "Prometheus response")
        if payload.get("status") != "success":
            error = payload.get("error")
            raise PrometheusQueryError(
                "Prometheus query did not succeed"
                + (f": {error}" if isinstance(error, str) and error else "")
            )
        data = parse_json_object(payload.get("data"), "Prometheus response data")
        result = _result_items(data.get("result"))
        body: dict[str, JsonValue] = {
            "query": query,
            "subject": subject or query,
            "result_type": _string_or_empty(data.get("resultType")),
            "sample_count": len(result),
        }
        value = _single_metric_value(result)
        if value is not None:
            body["metric_value"] = value
        return immutable_json(body)


def _base_url(value: str) -> httpx.URL:
    raw = parse_non_empty_string(value, "Prometheus base_url")
    url = httpx.URL(raw)
    if url.scheme not in {"http", "https"} or url.host is None:
        raise ValueError("Prometheus base_url must be an absolute http(s) URL")
    if url.query or url.fragment:
        raise ValueError("Prometheus base_url must not include query or fragment")
    return url.copy_with(path=url.path.rstrip("/"))


def _joined_path(base_path: str, request_path: str) -> str:
    if not base_path or base_path == "/":
        return "/" + request_path.lstrip("/")
    return base_path.rstrip("/") + "/" + request_path.lstrip("/")


def _result_items(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _single_metric_value(items: list[JsonValue]) -> float | None:
    if len(items) != 1:
        return None
    item = items[0]
    if not isinstance(item, dict):
        return None
    raw_value = item.get("value")
    if not isinstance(raw_value, list) or len(raw_value) < 2:
        return None
    value = raw_value[1]
    if not isinstance(value, str):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _string_or_empty(value: JsonValue) -> str:
    return value if isinstance(value, str) else ""
