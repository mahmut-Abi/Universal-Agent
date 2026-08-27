from __future__ import annotations

from types import MappingProxyType

import pytest
from pydantic import Field, ValidationError

from universal_agent.core.config_validation import (
    ConfigPayload,
    parse_bool,
    parse_int,
    parse_json_object,
    parse_json_object_sequence,
    parse_json_value,
    parse_non_empty_string_sequence,
    parse_optional_bool,
    parse_optional_int,
    parse_optional_string,
    parse_payload,
    parse_string,
    parse_string_sequence,
    pydantic_error_details,
)


class _ExamplePayload(ConfigPayload):
    name: str
    items: list[str] = Field(default_factory=list)


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
    with pytest.raises(ValueError, match=r"items\[1\] must be a string"):
        parse_string_sequence(["ok", 1], "items")


def test_parse_non_empty_string_sequence_reports_indexed_empty_values() -> None:
    assert parse_non_empty_string_sequence(["ready"], "checks") == ("ready",)

    with pytest.raises(ValueError, match=r"checks\[1\] must not be empty"):
        parse_non_empty_string_sequence(["ready", "  "], "checks")

    with pytest.raises(ValueError, match=r"checks\[1\] must be a non-empty string"):
        parse_non_empty_string_sequence(
            ["ready", ""],
            "checks",
            empty_template="{path} must be a non-empty string",
        )


def test_parse_scalar_helpers_use_strict_pydantic_validation() -> None:
    assert parse_string("ok", "name") == "ok"
    assert parse_string(None, "reason", default="default reason") == "default reason"
    assert parse_optional_string(None, "name") is None
    assert parse_optional_string("agent", "name") == "agent"
    assert parse_bool(True, "enabled") is True
    assert parse_optional_bool(True, "enabled") is True
    assert parse_optional_bool(None, "enabled") is None
    assert parse_int(None, "attempts", default=0) == 0
    assert parse_optional_int(3, "attempts") == 3
    assert parse_optional_int(None, "attempts") is None

    with pytest.raises(ValueError, match="name must be a string"):
        parse_string(None, "name")
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
