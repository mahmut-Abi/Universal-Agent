from __future__ import annotations

import pytest
from pydantic import Field

from universal_agent.core.config_validation import ConfigPayload, parse_json_value, parse_payload


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
