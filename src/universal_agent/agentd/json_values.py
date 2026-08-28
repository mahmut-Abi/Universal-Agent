from __future__ import annotations

from universal_agent.core import JsonValue, to_json_object, to_json_value


def _json_value(value: object) -> JsonValue:
    return to_json_value(value, fallback_to_string=True)


def _object_body(value: object) -> dict[str, JsonValue]:
    return to_json_object(value, fallback_to_string=True)
