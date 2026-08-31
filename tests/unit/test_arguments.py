from __future__ import annotations

import pytest

from universal_agent.core import ArgumentSchemaError, immutable_json, validate_argument_contract

SCHEMA = immutable_json(
    {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "count": {"type": "integer", "minimum": 1},
            "kind": {"type": "string", "enum": ["a", "b"]},
        },
        "required": ["name"],
        "additionalProperties": False,
    }
)


@pytest.mark.unit
def test_validate_argument_contract_missing_required_argument() -> None:
    error = validate_argument_contract(
        required_arguments=("name",),
        argument_schema=immutable_json(),
        arguments=immutable_json({}),
    )
    assert error == "missing required arguments: name"


@pytest.mark.unit
def test_validate_argument_contract_type_error() -> None:
    error = validate_argument_contract(
        required_arguments=(),
        argument_schema=SCHEMA,
        arguments=immutable_json({"name": "x", "count": "not-int"}),
    )
    assert error is not None
    assert "argument count must be integer" in error


@pytest.mark.unit
def test_validate_argument_contract_enum_error() -> None:
    error = validate_argument_contract(
        required_arguments=(),
        argument_schema=SCHEMA,
        arguments=immutable_json({"name": "x", "kind": "c"}),
    )
    assert error is not None
    assert "argument kind must be one of 'a', 'b'" in error


@pytest.mark.unit
def test_validate_argument_contract_range_error() -> None:
    error = validate_argument_contract(
        required_arguments=(),
        argument_schema=SCHEMA,
        arguments=immutable_json({"name": "x", "count": 0}),
    )
    assert error is not None
    assert "argument count must be >= 1" in error


@pytest.mark.unit
def test_validate_argument_contract_additional_properties_error() -> None:
    error = validate_argument_contract(
        required_arguments=(),
        argument_schema=SCHEMA,
        arguments=immutable_json({"name": "x", "extra": 1}),
    )
    assert error is not None
    assert "unexpected arguments: extra" in error


@pytest.mark.unit
def test_validate_argument_contract_accepts_valid_arguments() -> None:
    error = validate_argument_contract(
        required_arguments=(),
        argument_schema=SCHEMA,
        arguments=immutable_json({"name": "x", "count": 2, "kind": "a"}),
    )
    assert error is None


@pytest.mark.unit
def test_validate_argument_contract_empty_schema_passes() -> None:
    error = validate_argument_contract(
        required_arguments=(),
        argument_schema=immutable_json(),
        arguments=immutable_json({"anything": 1}),
    )
    assert error is None


@pytest.mark.unit
def test_validate_argument_contract_invalid_schema_returns_error_string() -> None:
    error = validate_argument_contract(
        required_arguments=(),
        argument_schema=immutable_json({"type": "not_a_real_type"}),
        arguments=immutable_json({"name": "x"}),
    )
    assert isinstance(error, str)
    assert "argument_schema is invalid" in error


@pytest.mark.unit
def test_argument_schema_error_is_value_error() -> None:
    assert issubclass(ArgumentSchemaError, ValueError)
