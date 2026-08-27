from __future__ import annotations

import pytest

from universal_agent.core.config_validation import parse_json_value


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
