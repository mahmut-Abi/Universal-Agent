from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from universal_agent.core import JsonMapping, JsonValue, immutable_json
from universal_agent.core.config_validation import parse_non_empty_string


class MetricsBackend(Protocol):
    async def query(self, arguments: JsonMapping) -> JsonMapping: ...


class StaticMetricsBackend:
    """Fixture-friendly metrics backend for local tests and examples."""

    def __init__(
        self,
        responses: Mapping[str, JsonMapping] | None = None,
        *,
        default_response: JsonMapping | None = None,
    ) -> None:
        self._responses = dict(responses or {})
        self._default_response = default_response
        self.calls: list[JsonMapping] = []

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
