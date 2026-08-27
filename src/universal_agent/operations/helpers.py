from __future__ import annotations

import hashlib
from datetime import datetime

from universal_agent.core import ErrorCode, JsonMapping, JsonValue
from universal_agent.security import redact_sensitive_mapping, redact_sensitive_value


def string(value: JsonValue | object) -> str:
    if isinstance(value, str):
        return value
    return ""


def error_code(value: JsonValue | object) -> ErrorCode | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return ErrorCode(value)
    except ValueError:
        return None


def non_negative_int(value: JsonValue | object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def duration_ms(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds() * 1000, 3)


def redacted_mapping(values: JsonMapping) -> JsonMapping:
    return redact_sensitive_mapping(values)


def redacted_value(key: str, value: object) -> JsonValue:
    return redact_sensitive_value(key, value)


def stable_hex(value: str, *, length: int) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def unix_nano(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def event_words(event_type: str) -> str:
    words: list[str] = []
    current = ""
    for character in event_type:
        if character.isupper() and current:
            words.append(current.lower())
            current = character
            continue
        current += character
    if current:
        words.append(current.lower())
    return " ".join(words) or event_type
