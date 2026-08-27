from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TextIO

from universal_agent.core import DomainIdentity, JsonValue, SuccessCriterion


class CliExit(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


def _parse_key_value_options(values: Sequence[str], label: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        key, separator, option_value = value.partition("=")
        if not separator or not key.strip() or not option_value.strip():
            raise ValueError(f"{label} must be KEY=VALUE")
        if key in parsed:
            raise ValueError(f"duplicate {label}: {key}")
        parsed[key] = option_value
    return parsed


def _success_criteria(values: Sequence[str]) -> tuple[SuccessCriterion, ...]:
    if not values:
        return (SuccessCriterion("healthy", True),)
    parsed: dict[str, JsonValue] = {}
    for value in values:
        key, separator, raw_expected = value.partition("=")
        if not separator or not key.strip() or not raw_expected.strip():
            raise ValueError("success criterion must be KEY=JSON")
        key = key.strip()
        if key in parsed:
            raise ValueError(f"duplicate success criterion: {key}")
        parsed[key] = _parse_success_json_value(raw_expected, key)
    return tuple(SuccessCriterion(key, expected) for key, expected in parsed.items())


def _parse_success_json_value(value: str, key: str) -> JsonValue:
    try:
        loaded: object = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"success criterion {key} must be valid JSON") from exc
    return _json_value(loaded, f"success.{key}")


def _json_value(value: object, field: str) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list):
        return [_json_value(item, f"{field}[]") for item in value]
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field} keys must be strings")
            result[key] = _json_value(item, f"{field}.{key}")
        return result
    raise ValueError(f"{field} must be JSON-compatible")


def _parse_domain_identity(value: str) -> DomainIdentity:
    if "@" not in value:
        raise ValueError(f"domain package dependency must be name@version: {value}")
    name, version = value.split("@", 1)
    if not name.strip() or not version.strip():
        raise ValueError(f"domain package dependency must be name@version: {value}")
    return DomainIdentity(name, version)


def _write_json(out: TextIO, payload: object) -> None:
    json.dump(_json_safe(payload), out, indent=2, sort_keys=True)
    out.write("\n")


def _write_text(out: TextIO, payload: str) -> None:
    out.write(payload)


def _write_error(out: TextIO, code: str, message: str) -> None:
    _write_json(out, {"error": {"code": code, "message": message}})


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe(item) for item in value]
    return str(value)


def _optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value == "true"


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("--before must be an ISO 8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError("--before must include a timezone")
    return parsed
