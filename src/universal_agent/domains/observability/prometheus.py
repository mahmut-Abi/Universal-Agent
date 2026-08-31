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
from universal_agent.domains.observability.backend import resource_subject_from_labels


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


_MAX_RESOURCE_SUBJECTS = 20


class PrometheusBackend:
    """Read-only Prometheus backend: instant queries, range queries, rules and alerts."""

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
        data = await self._successful_data("/api/v1/query", {"query": query})
        result = _result_items(data.get("result"))
        body: dict[str, JsonValue] = {
            "query": query,
            "result_type": _string_or_empty(data.get("resultType")),
            "sample_count": len(result),
        }
        if subject is not None:
            body["subject"] = subject
        resource_subject = resource_subject_from_labels(_single_metric_labels(result))
        if resource_subject is not None:
            body["resource_subject"] = resource_subject
        value = _single_metric_value(result)
        if value is not None:
            body["metric_value"] = value
        return immutable_json(body)

    async def query_range(self, arguments: JsonMapping) -> JsonMapping:
        query = parse_non_empty_string(arguments.get("query"), "query")
        start = parse_non_empty_string(arguments.get("start"), "start")
        end = parse_non_empty_string(arguments.get("end"), "end")
        step = parse_non_empty_string(arguments.get("step"), "step")
        subject = parse_optional_non_empty_string(arguments.get("subject"), "subject")
        data = await self._successful_data(
            "/api/v1/query_range",
            {"query": query, "start": start, "end": end, "step": step},
        )
        result = _result_items(data.get("result"))
        body: dict[str, JsonValue] = {
            "query": query,
            "result_type": _string_or_empty(data.get("resultType")),
            "series_count": len(result),
            "samples_total": sum(_series_sample_count(item) for item in result),
        }
        if subject is not None:
            body["subject"] = subject
        resource_subject = resource_subject_from_labels(_first_series_labels(result))
        if resource_subject is not None:
            body["resource_subject"] = resource_subject
        first_value, last_value = _series_bounds(result)
        if first_value is not None:
            body["first_value"] = first_value
        if last_value is not None:
            body["last_value"] = last_value
        return immutable_json(body)

    async def rules(self, arguments: JsonMapping) -> JsonMapping:
        data = await self._successful_data("/api/v1/rules", {})
        rule_count = alerting_count = recording_count = 0
        unhealthy_count = firing_count = 0
        for group in _list_items(data.get("groups")):
            if not isinstance(group, dict):
                continue
            for rule in _list_items(group.get("rules")):
                if not isinstance(rule, dict):
                    continue
                rule_count += 1
                rule_type = _string_or_empty(rule.get("type"))
                if rule_type == "alerting":
                    alerting_count += 1
                elif rule_type == "recording":
                    recording_count += 1
                health = _string_or_empty(rule.get("health"))
                if health and health != "ok":
                    unhealthy_count += 1
                for alert in _list_items(rule.get("alerts")):
                    if isinstance(alert, dict) and _string_or_empty(alert.get("state")) == "firing":
                        firing_count += 1
        return immutable_json(
            {
                "rule_count": rule_count,
                "alerting_rule_count": alerting_count,
                "recording_rule_count": recording_count,
                "unhealthy_rule_count": unhealthy_count,
                "firing_alert_count": firing_count,
            }
        )

    async def alerts(self, arguments: JsonMapping) -> JsonMapping:
        data = await self._successful_data("/api/v1/alerts", {})
        items = _list_items(data.get("alerts"))
        firing_count = pending_count = 0
        resource_subjects: list[JsonValue] = []
        for alert in items:
            if not isinstance(alert, dict):
                continue
            state = _string_or_empty(alert.get("state"))
            if state == "firing":
                firing_count += 1
            elif state == "pending":
                pending_count += 1
            labels = alert.get("labels")
            if not isinstance(labels, dict):
                continue
            resource_subject = resource_subject_from_labels(labels)
            if resource_subject is not None and resource_subject not in resource_subjects:
                resource_subjects.append(resource_subject)
        body: dict[str, JsonValue] = {
            "alert_count": len(items),
            "firing_alert_count": firing_count,
            "pending_alert_count": pending_count,
        }
        if resource_subjects:
            resource_subjects.sort(key=str)
            body["resource_subjects"] = resource_subjects[:_MAX_RESOURCE_SUBJECTS]
        return immutable_json(body)

    async def _successful_data(self, path: str, params: Mapping[str, str]) -> JsonMapping:
        response = await self._transport.request(
            path,
            query=params,
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
        return parse_json_object(payload.get("data"), "Prometheus response data")


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


def _list_items(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _single_metric_labels(items: list[JsonValue]) -> JsonMapping | None:
    if len(items) != 1:
        return None
    item = items[0]
    if not isinstance(item, dict):
        return None
    labels = item.get("metric")
    return labels if isinstance(labels, dict) else None


def _first_series_labels(items: list[JsonValue]) -> JsonMapping | None:
    for item in items:
        if not isinstance(item, dict):
            continue
        labels = item.get("metric")
        if isinstance(labels, dict):
            return labels
    return None


def _series_sample_count(item: JsonValue) -> int:
    if not isinstance(item, dict):
        return 0
    values = item.get("values")
    return len(values) if isinstance(values, list) else 0


def _series_bounds(items: list[JsonValue]) -> tuple[float | None, float | None]:
    for item in items:
        if not isinstance(item, dict):
            continue
        values = item.get("values")
        if not isinstance(values, list) or not values:
            continue
        first = _sample_value(values[0])
        last = _sample_value(values[-1])
        if first is not None or last is not None:
            return first, last
    return None, None


def _sample_value(sample: JsonValue) -> float | None:
    if not isinstance(sample, list) or len(sample) < 2:
        return None
    raw_value = sample[1]
    if not isinstance(raw_value, str):
        return None
    try:
        return float(raw_value)
    except ValueError:
        return None


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
