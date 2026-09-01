"""Lightweight JSON codec and validation helpers for the client SDK.

Deliberately independent from the universal_agent kernel: these helpers are a
small, dependency-free subset of the kernel's codec/validation surface, kept
separate so the SDK ships standalone.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from universal_agent_api.types import JsonMapping, JsonValue


class JsonCodecError(ValueError):
    """Raised when a JSON payload cannot be encoded or decoded."""


def dumps_json(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise JsonCodecError(f"value is not JSON serializable: {exc}") from exc


def loads_json(value: str | bytes | bytearray) -> object:
    try:
        return json.loads(value)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise JsonCodecError(f"invalid JSON: {exc}") from exc


def immutable_json(values: Mapping[str, JsonValue] | None = None) -> JsonMapping:
    return dict(values) if values else {}


def parse_json_object(value: object, field: str) -> JsonMapping:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a JSON object")
    result: dict[str, JsonValue] = {str(key): cast(JsonValue, item) for key, item in value.items()}
    return result


def parse_non_empty_string(
    value: object,
    field: str,
    *,
    empty_template: str = "{path} must not be empty",
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(empty_template.format(path=field))
    return value


def parse_positive_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if number <= 0:
        raise ValueError(f"{field} must be positive")
    return number


__all__ = [
    "JsonCodecError",
    "dumps_json",
    "immutable_json",
    "loads_json",
    "parse_json_object",
    "parse_non_empty_string",
    "parse_positive_float",
]
