from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from universal_agent.core import DateTimeParseError, parse_iso_datetime


@pytest.mark.unit
def test_parse_iso_datetime_parses_with_timezone() -> None:
    parsed = parse_iso_datetime("2026-01-15T10:30:00Z", field="started_at")
    assert parsed == datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)


@pytest.mark.unit
def test_parse_iso_datetime_parses_with_offset() -> None:
    parsed = parse_iso_datetime(
        "2026-01-15T10:30:00+02:00",
        field="started_at",
        description="a start time",
    )
    assert parsed.utcoffset() == timedelta(hours=2)


@pytest.mark.unit
def test_parse_iso_datetime_rejects_garbage() -> None:
    with pytest.raises(DateTimeParseError):
        parse_iso_datetime("not-a-date", field="started_at")


@pytest.mark.unit
def test_parse_iso_datetime_rejects_non_string() -> None:
    with pytest.raises(DateTimeParseError):
        parse_iso_datetime("2026-13-40", field="started_at")


@pytest.mark.unit
def test_parse_iso_datetime_requires_timezone_when_requested() -> None:
    with pytest.raises(DateTimeParseError, match="must include a timezone"):
        parse_iso_datetime(
            "2026-01-15T10:30:00",
            field="started_at",
            require_timezone=True,
        )


@pytest.mark.unit
def test_parse_iso_datetime_accepts_timezone_when_required() -> None:
    parsed = parse_iso_datetime(
        "2026-01-15T10:30:00+00:00",
        field="started_at",
        require_timezone=True,
    )
    assert parsed.tzinfo is not None


@pytest.mark.unit
def test_date_time_parse_error_is_value_error() -> None:
    assert issubclass(DateTimeParseError, ValueError)
