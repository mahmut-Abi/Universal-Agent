from __future__ import annotations

from universal_agent.core import JsonMapping, JsonValue, immutable_json
from universal_agent.operations.helpers import stable_hex, unix_nano
from universal_agent.operations.views import RuntimeTraceSpanView


def build_opentelemetry_trace_export(
    spans: tuple[RuntimeTraceSpanView, ...],
    *,
    service_name: str = "universal-agent-runtime",
    scope_name: str = "universal-agent-runtime",
    scope_version: str = "0.1.0",
) -> JsonMapping:
    """Project runtime trace spans into an OTLP JSON-compatible payload.

    The Runtime remains the source of trace semantics. This function is a
    product adapter for collectors and tests: it derives stable hex trace/span
    IDs from runtime IDs, carries redacted attributes forward, and avoids
    adding an OpenTelemetry dependency to the Kernel.
    """
    return immutable_json(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "service.name",
                                "value": {"stringValue": service_name},
                            }
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": scope_name, "version": scope_version},
                            "spans": [_otlp_span(span) for span in spans],
                        }
                    ],
                }
            ]
        }
    )


def _otlp_span(span: RuntimeTraceSpanView) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "traceId": stable_hex(span.trace_id, length=32),
        "spanId": stable_hex(span.span_id, length=16),
        "name": span.name,
        "kind": _otlp_span_kind(span.kind),
        "startTimeUnixNano": str(unix_nano(span.start_time)),
        "endTimeUnixNano": str(unix_nano(span.end_time)),
        "status": {
            "code": _otlp_status_code(span.status),
            "message": span.status,
        },
        "attributes": _otlp_attributes(
            {
                "runtime.session_id": str(span.session_id),
                "runtime.goal_id": str(span.goal_id),
                "runtime.task_id": str(span.task_id),
                "runtime.action_id": None if span.action_id is None else str(span.action_id),
                **dict(span.attributes),
            }
        ),
    }
    if span.parent_span_id is not None:
        payload["parentSpanId"] = stable_hex(span.parent_span_id, length=16)
    return payload


def _otlp_attributes(values: dict[str, JsonValue]) -> list[JsonValue]:
    return [
        {"key": key, "value": _otlp_any_value(value)}
        for key, value in sorted(values.items())
        if value is not None
    ]


def _otlp_any_value(value: JsonValue) -> dict[str, JsonValue]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [_otlp_any_value(item) for item in value]}}
    if isinstance(value, dict):
        return {
            "kvlistValue": {
                "values": [
                    {"key": str(key), "value": _otlp_any_value(item)}
                    for key, item in sorted(value.items())
                ]
            }
        }
    return {"stringValue": str(value)}


def _otlp_span_kind(kind: str) -> str:
    if kind == "client":
        return "SPAN_KIND_CLIENT"
    if kind == "server":
        return "SPAN_KIND_SERVER"
    return "SPAN_KIND_INTERNAL"


def _otlp_status_code(status: str) -> str:
    if status == "ok":
        return "STATUS_CODE_OK"
    if status == "error":
        return "STATUS_CODE_ERROR"
    return "STATUS_CODE_UNSET"
