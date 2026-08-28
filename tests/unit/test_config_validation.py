from __future__ import annotations

from types import MappingProxyType

import pytest
from pydantic import Field, ValidationError

from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticNonEmptyString,
    parse_bool,
    parse_bool_text,
    parse_bounded_float,
    parse_bounded_float_text,
    parse_bounded_int,
    parse_int,
    parse_json_object,
    parse_json_object_sequence,
    parse_json_value,
    parse_lower_sha256_hex_digest,
    parse_non_empty_string,
    parse_non_empty_string_sequence,
    parse_non_negative_float,
    parse_non_negative_int,
    parse_non_negative_int_text,
    parse_optional_bool,
    parse_optional_int,
    parse_optional_lower_sha256_hex_digest,
    parse_optional_non_empty_string,
    parse_optional_non_negative_float,
    parse_optional_non_negative_int,
    parse_optional_positive_float,
    parse_optional_rate,
    parse_optional_string,
    parse_payload,
    parse_positive_float,
    parse_positive_int,
    parse_positive_int_text,
    parse_rate,
    parse_string,
    parse_string_sequence,
    parse_unique_non_empty_string_sequence,
    pydantic_error_details,
)


class _ExamplePayload(ConfigPayload):
    name: str
    items: list[str] = Field(default_factory=list)


class _NestedNonEmptyPayload(ConfigPayload):
    name: PydanticNonEmptyString


class _NestedPayload(ConfigPayload):
    item: _NestedNonEmptyPayload


def test_parse_json_value_accepts_nested_json_values() -> None:
    assert parse_json_value(
        {
            "healthy": True,
            "replicas": 3,
            "reasons": ["available", None],
            "metadata": {"namespace": "prod"},
        },
        "payload",
    ) == {
        "healthy": True,
        "replicas": 3,
        "reasons": ["available", None],
        "metadata": {"namespace": "prod"},
    }


def test_parse_json_value_rejects_non_json_values() -> None:
    with pytest.raises(ValueError, match="payload must be JSON-compatible"):
        parse_json_value(object(), "payload")


def test_parse_json_object_accepts_immutable_mappings() -> None:
    assert parse_json_object(MappingProxyType({"replicas": 3}), "payload") == {"replicas": 3}


def test_parse_json_object_sequence_accepts_lists_of_immutable_mappings() -> None:
    assert parse_json_object_sequence(
        [MappingProxyType({"name": "agent-a"}), {"name": "agent-b"}],
        "agents",
    ) == ({"name": "agent-a"}, {"name": "agent-b"})


def test_parse_string_sequence_reports_indexed_errors() -> None:
    assert parse_string_sequence(("ok", "done"), "items") == ("ok", "done")

    with pytest.raises(ValueError, match=r"items\[1\] must be a string"):
        parse_string_sequence(["ok", 1], "items")


def test_parse_non_empty_string_sequence_reports_indexed_empty_values() -> None:
    assert parse_non_empty_string_sequence(["ready"], "checks") == ("ready",)
    assert parse_non_empty_string_sequence(("ready", "healthy"), "checks") == (
        "ready",
        "healthy",
    )

    with pytest.raises(ValueError, match=r"checks\[1\] must not be empty"):
        parse_non_empty_string_sequence(["ready", "  "], "checks")

    with pytest.raises(ValueError, match=r"checks\[1\] must be a non-empty string"):
        parse_non_empty_string_sequence(
            ["ready", ""],
            "checks",
            empty_template="{path} must be a non-empty string",
        )


def test_parse_unique_non_empty_string_sequence_reports_duplicates() -> None:
    assert parse_unique_non_empty_string_sequence(("smoke", "kubernetes"), "tags") == (
        "smoke",
        "kubernetes",
    )

    with pytest.raises(ValueError, match="duplicate tags: smoke"):
        parse_unique_non_empty_string_sequence(("smoke", "smoke"), "tags")

    with pytest.raises(ValueError, match=r"tags\[1\] must not contain empty values"):
        parse_unique_non_empty_string_sequence(
            ("smoke", " "),
            "tags",
            empty_template="{path} must not contain empty values",
        )


def test_parse_lower_sha256_hex_digest_uses_pydantic_pattern_validation() -> None:
    digest = "a" * 64

    assert parse_lower_sha256_hex_digest(digest, "manifest_sha256") == digest
    assert parse_optional_lower_sha256_hex_digest(None, "manifest_sha256") == ""
    assert parse_optional_lower_sha256_hex_digest("", "manifest_sha256") == ""

    with pytest.raises(
        ValueError,
        match="manifest_sha256 must be a lowercase SHA-256 hex digest",
    ):
        parse_lower_sha256_hex_digest("A" * 64, "manifest_sha256")
    with pytest.raises(ValueError, match="manifest_sha256 must be a string"):
        parse_lower_sha256_hex_digest(123, "manifest_sha256")


def test_parse_numeric_helpers_use_pydantic_range_validation() -> None:
    assert parse_non_negative_int(0, "count") == 0
    assert parse_non_negative_float(1, "cost") == 1.0
    assert parse_optional_non_negative_float(None, "cost") is None
    assert parse_optional_non_negative_float(1.25, "cost") == 1.25
    assert parse_optional_non_negative_int(None, "count") is None
    assert parse_optional_non_negative_int(2, "count") == 2
    assert parse_positive_int(1, "limit") == 1
    assert parse_bounded_int(3, "replicas", minimum=1, maximum=10) == 3
    assert parse_positive_float(0.1, "timeout") == 0.1
    assert parse_bounded_float(0.25, "timeout_seconds", minimum=0.0, maximum=30.0) == 0.25
    assert parse_optional_positive_float(None, "timeout") is None
    assert parse_rate(0.0, "pass_rate") == 0.0
    assert parse_rate(1, "pass_rate") == 1.0
    assert parse_optional_rate(None, "pass_rate") is None

    with pytest.raises(ValueError, match="count must not be negative"):
        parse_non_negative_int(-1, "count")
    with pytest.raises(ValueError, match="cost must be non-negative"):
        parse_non_negative_float(-0.1, "cost")
    with pytest.raises(ValueError, match="limit must be positive"):
        parse_positive_int(0, "limit")
    with pytest.raises(ValueError, match="replicas must be an integer"):
        parse_bounded_int(True, "replicas", minimum=1, maximum=10)
    with pytest.raises(ValueError, match="replicas must be between 1 and 10"):
        parse_bounded_int(0, "replicas", minimum=1, maximum=10)
    with pytest.raises(ValueError, match="timeout must be positive"):
        parse_positive_float(0.0, "timeout")
    with pytest.raises(ValueError, match="timeout_seconds must be a number"):
        parse_bounded_float("0.25", "timeout_seconds", minimum=0.0, maximum=30.0)
    with pytest.raises(ValueError, match="timeout_seconds must be between 0 and 30"):
        parse_bounded_float(31.0, "timeout_seconds", minimum=0.0, maximum=30.0)
    with pytest.raises(ValueError, match="count must be an integer"):
        parse_non_negative_int(True, "count")
    with pytest.raises(ValueError, match="cost must be a number"):
        parse_non_negative_float("1.0", "cost")
    with pytest.raises(ValueError, match=r"pass_rate must be between 0\.0 and 1\.0"):
        parse_rate(1.1, "pass_rate")
    with pytest.raises(ValueError, match="pass_rate must be a number"):
        parse_rate(True, "pass_rate")


def test_parse_text_numeric_helpers_use_pydantic_string_validation() -> None:
    assert parse_bool_text("yes", "wait") is True
    assert parse_bool_text("0", "wait") is False
    assert parse_non_negative_int_text("0", "content-length") == 0
    assert parse_positive_int_text("10", "limit") == 10
    assert (
        parse_bounded_float_text(
            "0.25",
            "timeout_seconds",
            minimum=0.0,
            maximum=30.0,
        )
        == 0.25
    )

    with pytest.raises(ValueError, match="wait must be a boolean"):
        parse_bool_text("maybe", "wait")
    with pytest.raises(ValueError, match="content-length must be an integer"):
        parse_non_negative_int_text("abc", "content-length")
    with pytest.raises(ValueError, match="content-length must be non-negative"):
        parse_non_negative_int_text("-1", "content-length")
    with pytest.raises(ValueError, match="limit must be a positive integer"):
        parse_positive_int_text("0", "limit")
    with pytest.raises(ValueError, match="timeout_seconds must be a number"):
        parse_bounded_float_text(
            "abc",
            "timeout_seconds",
            minimum=0.0,
            maximum=30.0,
        )
    with pytest.raises(ValueError, match="timeout_seconds must be between 0 and 30"):
        parse_bounded_float_text(
            "31",
            "timeout_seconds",
            minimum=0.0,
            maximum=30.0,
        )


def test_parse_scalar_helpers_use_strict_pydantic_validation() -> None:
    assert parse_string("ok", "name") == "ok"
    assert parse_string(None, "reason", default="default reason") == "default reason"
    assert parse_optional_string(None, "name") is None
    assert parse_optional_string("agent", "name") == "agent"
    assert parse_non_empty_string("agent", "name") == "agent"
    assert parse_optional_non_empty_string("agent", "name") == "agent"
    assert parse_optional_non_empty_string(None, "name") is None
    assert parse_bool(True, "enabled") is True
    assert parse_optional_bool(True, "enabled") is True
    assert parse_optional_bool(None, "enabled") is None
    assert parse_int(None, "attempts", default=0) == 0
    assert parse_optional_int(3, "attempts") == 3
    assert parse_optional_int(None, "attempts") is None

    with pytest.raises(ValueError, match="name must be a string"):
        parse_string(None, "name")
    with pytest.raises(ValueError, match="name must not be empty"):
        parse_non_empty_string("  ", "name")
    with pytest.raises(ValueError, match="name must be a non-empty string"):
        parse_optional_non_empty_string(
            "",
            "name",
            empty_template="{path} must be a non-empty string",
        )
    with pytest.raises(ValueError, match="enabled must be a boolean"):
        parse_bool("yes", "enabled")
    with pytest.raises(ValueError, match="enabled must be a boolean"):
        parse_optional_bool("yes", "enabled")
    with pytest.raises(ValueError, match="attempts must be an integer"):
        parse_int(True, "attempts")
    with pytest.raises(ValueError, match="attempts must be an integer"):
        parse_optional_int(True, "attempts")


def test_parse_payload_uses_custom_missing_template() -> None:
    with pytest.raises(ValueError, match="missing required field: name"):
        parse_payload(_ExamplePayload, {}, missing_template="missing required field: {path}")


def test_parse_payload_formats_common_pydantic_type_errors() -> None:
    with pytest.raises(ValueError, match="items must be a list"):
        parse_payload(_ExamplePayload, {"name": "ok", "items": "bad"})


def test_parse_payload_prefixes_generic_value_error_paths() -> None:
    with pytest.raises(ValueError, match="item\\.name must not be empty"):
        parse_payload(_NestedPayload, {"item": {"name": " "}})


def test_parse_payload_prefixes_error_paths_and_accepts_expected_type_overrides() -> None:
    with pytest.raises(ValueError, match="provider\\.items must be custom list"):
        parse_payload(
            _ExamplePayload,
            {"name": "ok", "items": "bad"},
            field="provider",
            expected_types={"list_type": "custom list"},
        )


def test_pydantic_error_details_exposes_first_error_path_type_and_message() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _ExamplePayload.model_validate({"name": "ok", "items": ["good", 1]})

    details = pydantic_error_details(exc_info.value, "payload")

    assert details.path == "payload.items[1]"
    assert details.error_type == "string_type"
    assert details.message
