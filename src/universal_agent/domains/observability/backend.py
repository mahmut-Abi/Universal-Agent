from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from universal_agent.core import JsonMapping, JsonValue, immutable_json
from universal_agent.core.config_validation import parse_non_empty_string


class MetricsBackend(Protocol):
    async def query(self, arguments: JsonMapping) -> JsonMapping: ...

    async def query_range(self, arguments: JsonMapping) -> JsonMapping: ...

    async def rules(self, arguments: JsonMapping) -> JsonMapping: ...

    async def alerts(self, arguments: JsonMapping) -> JsonMapping: ...


class StaticMetricsBackend:
    """Fixture-friendly metrics backend for local tests and examples."""

    def __init__(
        self,
        responses: Mapping[str, JsonMapping] | None = None,
        *,
        default_response: JsonMapping | None = None,
        range_responses: Mapping[str, JsonMapping] | None = None,
        default_range_response: JsonMapping | None = None,
        rules_response: JsonMapping | None = None,
        alerts_response: JsonMapping | None = None,
    ) -> None:
        self._responses = dict(responses or {})
        self._default_response = default_response
        self._range_responses = dict(range_responses or {})
        self._default_range_response = default_range_response
        self._rules_response = rules_response
        self._alerts_response = alerts_response
        self.calls: list[JsonMapping] = []
        self.range_calls: list[JsonMapping] = []
        self.rule_calls: list[JsonMapping] = []
        self.alert_calls: list[JsonMapping] = []

    async def query(self, arguments: JsonMapping) -> JsonMapping:
        query = parse_non_empty_string(arguments.get("query"), "query")
        self.calls.append(immutable_json(arguments))
        response = self._responses.get(query, self._default_response)
        body: dict[str, JsonValue] = {"query": query}
        subject = arguments.get("subject") or arguments.get("resource") or arguments.get("service")
        if isinstance(subject, str) and subject.strip():
            body["subject"] = subject.strip()
        if response is not None:
            body.update(response)
        else:
            body["sample_count"] = 0
        return immutable_json(body)

    async def query_range(self, arguments: JsonMapping) -> JsonMapping:
        query = parse_non_empty_string(arguments.get("query"), "query")
        self.range_calls.append(immutable_json(arguments))
        response = self._range_responses.get(query, self._default_range_response)
        body: dict[str, JsonValue] = {"query": query, "result_type": "matrix"}
        subject = arguments.get("subject") or arguments.get("resource") or arguments.get("service")
        if isinstance(subject, str) and subject.strip():
            body["subject"] = subject.strip()
        if response is not None:
            body.update(response)
        else:
            body["series_count"] = 0
            body["samples_total"] = 0
        return immutable_json(body)

    async def rules(self, arguments: JsonMapping) -> JsonMapping:
        self.rule_calls.append(immutable_json(arguments))
        response = self._rules_response or {
            "rule_count": 0,
            "alerting_rule_count": 0,
            "recording_rule_count": 0,
        }
        return immutable_json(response)

    async def alerts(self, arguments: JsonMapping) -> JsonMapping:
        self.alert_calls.append(immutable_json(arguments))
        response = self._alerts_response or {
            "alert_count": 0,
            "firing_alert_count": 0,
        }
        return immutable_json(response)


def resource_subject_from_labels(labels: JsonMapping | None) -> str | None:
    """Derive a world-model resource subject from Prometheus metric labels.

    The mapping follows the Kubernetes label conventions used by the
    Kubernetes domain (``pod``, ``deployment``, ``service``, ``namespace``)
    so telemetry lands on the same world identity the workload domain
    already writes. First matching rule wins; unknown or missing label sets
    map to ``None`` and callers keep their fallback subject.
    """
    if not labels:
        return None
    for label, prefix in (
        ("pod", "pod"),
        ("deployment", "deployment"),
        ("service", "service"),
        ("namespace", "namespace"),
    ):
        value = labels.get(label)
        if isinstance(value, str) and value.strip():
            return f"{prefix}/{value.strip()}"
    return None
