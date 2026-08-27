from __future__ import annotations

from typing import Any

from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.common.v1.common_pb2 import (
    AnyValue,
    ArrayValue,
    InstrumentationScope,
    KeyValue,
    KeyValueList,
)
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import (
    ResourceSpans,
    ScopeSpans,
    Span,
    Status,
    TracesData,
)

from universal_agent.core import JsonMapping, JsonValue, immutable_json
from universal_agent.core.config_validation import parse_json_object
from universal_agent.operations.helpers import stable_hex, unix_nano
from universal_agent.operations.views import RuntimeTraceSpanView


def build_opentelemetry_trace_export(
    spans: tuple[RuntimeTraceSpanView, ...],
    *,
    service_name: str = "universal-agent-runtime",
    scope_name: str = "universal-agent-runtime",
    scope_version: str = "0.1.0",
) -> JsonMapping:
    """Project runtime trace spans into an OTLP JSON-compatible payload."""

    message = TracesData(
        resource_spans=[
            ResourceSpans(
                resource=Resource(
                    attributes=[
                        _otlp_key_value("service.name", service_name),
                    ]
                ),
                scope_spans=[
                    ScopeSpans(
                        scope=InstrumentationScope(
                            name=scope_name,
                            version=scope_version,
                        ),
                        spans=[_otlp_span_message(span) for span in spans],
                    )
                ],
            )
        ]
    )
    payload = MessageToDict(message)
    _restore_hex_span_ids(payload, spans)
    return immutable_json(parse_json_object(payload, "opentelemetry_traces"))


def _otlp_span_message(span: RuntimeTraceSpanView) -> Span:
    message = Span(
        trace_id=bytes.fromhex(stable_hex(span.trace_id, length=32)),
        span_id=bytes.fromhex(stable_hex(span.span_id, length=16)),
        name=span.name,
        kind=_otlp_span_kind(span.kind),
        start_time_unix_nano=unix_nano(span.start_time),
        end_time_unix_nano=unix_nano(span.end_time),
        status=Status(
            code=_otlp_status_code(span.status),
            message=span.status,
        ),
        attributes=_otlp_attributes(
            {
                "runtime.session_id": str(span.session_id),
                "runtime.goal_id": str(span.goal_id),
                "runtime.task_id": str(span.task_id),
                "runtime.action_id": None if span.action_id is None else str(span.action_id),
                **dict(span.attributes),
            }
        ),
    )
    if span.parent_span_id is not None:
        message.parent_span_id = bytes.fromhex(stable_hex(span.parent_span_id, length=16))
    return message


def _otlp_attributes(values: dict[str, JsonValue]) -> list[KeyValue]:
    return [
        _otlp_key_value(key, value)
        for key, value in sorted(values.items())
        if value is not None
    ]


def _otlp_key_value(key: str, value: JsonValue) -> KeyValue:
    return KeyValue(key=key, value=_otlp_any_value(value))


def _otlp_any_value(value: JsonValue) -> AnyValue:
    if isinstance(value, bool):
        return AnyValue(bool_value=value)
    if isinstance(value, int):
        return AnyValue(int_value=value)
    if isinstance(value, float):
        return AnyValue(double_value=value)
    if isinstance(value, str):
        return AnyValue(string_value=value)
    if isinstance(value, list):
        return AnyValue(array_value=ArrayValue(values=[_otlp_any_value(item) for item in value]))
    if isinstance(value, dict):
        return AnyValue(
            kvlist_value=KeyValueList(
                values=[
                    _otlp_key_value(str(key), item)
                    for key, item in sorted(value.items())
                ]
            )
        )
    return AnyValue(string_value=str(value))


def _otlp_span_kind(kind: str) -> Span.SpanKind.ValueType:
    if kind == "client":
        return Span.SPAN_KIND_CLIENT
    if kind == "server":
        return Span.SPAN_KIND_SERVER
    return Span.SPAN_KIND_INTERNAL


def _otlp_status_code(status: str) -> Status.StatusCode.ValueType:
    if status == "ok":
        return Status.STATUS_CODE_OK
    if status == "error":
        return Status.STATUS_CODE_ERROR
    return Status.STATUS_CODE_UNSET


def _restore_hex_span_ids(
    payload: dict[str, Any],
    spans: tuple[RuntimeTraceSpanView, ...],
) -> None:
    """Keep the existing Runtime API hex ID contract after protobuf JSON conversion."""

    scope_span = _scope_span_payload(payload)
    if scope_span is None:
        return
    raw_spans = scope_span.setdefault("spans", [])
    exported_spans = _exported_spans(raw_spans)
    for exported, span in zip(exported_spans, spans, strict=True):
        exported["traceId"] = stable_hex(span.trace_id, length=32)
        exported["spanId"] = stable_hex(span.span_id, length=16)
        if span.parent_span_id is not None:
            exported["parentSpanId"] = stable_hex(span.parent_span_id, length=16)


def _scope_span_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    resource_spans = payload.get("resourceSpans")
    if not isinstance(resource_spans, list) or not resource_spans:
        return None
    resource_span = resource_spans[0]
    if not isinstance(resource_span, dict):
        return None
    scope_spans = resource_span.get("scopeSpans")
    if not isinstance(scope_spans, list) or not scope_spans:
        return None
    scope_span = scope_spans[0]
    if not isinstance(scope_span, dict):
        return None
    return scope_span


def _exported_spans(spans: object) -> list[dict[str, Any]]:
    if not isinstance(spans, list):
        return []
    return [span for span in spans if isinstance(span, dict)]
