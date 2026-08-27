from __future__ import annotations

from datetime import datetime

from dateutil.parser import isoparse


class DateTimeParseError(ValueError):
    pass


def parse_iso_datetime(
    value: str,
    *,
    field: str,
    description: str = "an ISO datetime string",
    require_timezone: bool = False,
) -> datetime:
    try:
        parsed = isoparse(value)
    except (TypeError, ValueError) as exc:
        raise DateTimeParseError(f"{field} must be {description}") from exc
    if require_timezone and parsed.tzinfo is None:
        raise DateTimeParseError(f"{field} must include a timezone")
    return parsed
