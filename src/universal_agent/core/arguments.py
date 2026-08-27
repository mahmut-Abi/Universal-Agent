from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from universal_agent.core.models import JsonMapping, JsonValue


class ArgumentSchemaError(ValueError):
    pass


def validate_argument_contract(
    *,
    required_arguments: tuple[str, ...],
    argument_schema: JsonMapping,
    arguments: JsonMapping,
) -> str | None:
    """Validate arguments against the deterministic runtime input contract."""

    missing = [name for name in required_arguments if name not in arguments]
    if missing:
        return f"missing required arguments: {', '.join(missing)}"
    if not argument_schema:
        return None
    try:
        return _validate_argument_schema(
            _plain_json_object(argument_schema),
            _plain_json_object(arguments),
        )
    except ArgumentSchemaError as exc:
        return str(exc)


def _validate_argument_schema(
    schema: dict[str, Any],
    arguments: dict[str, Any],
) -> str | None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ArgumentSchemaError(f"argument_schema is invalid: {exc.message}") from exc

    validator = Draft202012Validator(schema)
    errors = tuple(sorted(validator.iter_errors(arguments), key=_validation_error_key))
    if not errors:
        return None
    required_error = _required_error(errors, arguments)
    if required_error is not None:
        return required_error
    return _format_validation_error(errors[0])


def _required_error(
    errors: tuple[ValidationError, ...],
    arguments: Mapping[str, Any],
) -> str | None:
    for error in errors:
        if error.validator != "required":
            continue
        instance = _instance_at_path(arguments, tuple(error.path))
        required = error.validator_value
        if not isinstance(instance, Mapping) or not isinstance(required, list):
            return _format_validation_error(error)
        missing = [item for item in required if isinstance(item, str) and item not in instance]
        if not missing:
            return _format_validation_error(error)
        if not error.path:
            return f"missing required arguments: {', '.join(missing)}"
        return (
            f"argument {_path_text(tuple(error.path))} missing required properties: "
            f"{', '.join(missing)}"
        )
    return None


def _format_validation_error(error: ValidationError) -> str:
    path = _path_text(tuple(error.path))
    if error.validator == "type":
        return f"argument {path} must be {_type_text(error.validator_value)}"
    if error.validator == "enum":
        return f"argument {path} must be one of {_enum_text(error.validator_value)}"
    if error.validator == "minimum":
        return f"argument {path} must be >= {_number_text(error.validator_value)}"
    if error.validator == "maximum":
        return f"argument {path} must be <= {_number_text(error.validator_value)}"
    if error.validator == "minLength":
        return f"argument {path} length must be >= {_number_text(error.validator_value)}"
    if error.validator == "maxLength":
        return f"argument {path} length must be <= {_number_text(error.validator_value)}"
    if error.validator == "minItems":
        return f"argument {path} length must be >= {_number_text(error.validator_value)}"
    if error.validator == "maxItems":
        return f"argument {path} length must be <= {_number_text(error.validator_value)}"
    if error.validator == "additionalProperties":
        unexpected = _unexpected_properties(error)
        if not error.path:
            return f"unexpected arguments: {', '.join(unexpected)}"
        return f"argument {path} has unexpected properties: {', '.join(unexpected)}"
    if error.validator == "required":
        instance = error.instance if isinstance(error.instance, Mapping) else {}
        return _required_error((error,), instance) or str(error.message)
    if path:
        return f"argument {path}: {error.message}"
    return str(error.message)


def _unexpected_properties(error: ValidationError) -> tuple[str, ...]:
    if not isinstance(error.instance, Mapping):
        return (error.message,)
    schema = error.schema
    properties = schema.get("properties", {}) if isinstance(schema, Mapping) else {}
    allowed = set(properties) if isinstance(properties, Mapping) else set()
    unexpected = tuple(
        sorted(key for key in error.instance if isinstance(key, str) and key not in allowed)
    )
    return unexpected or (error.message,)


def _validation_error_key(error: ValidationError) -> tuple[int, str, str]:
    priority = {
        "required": 0,
        "additionalProperties": 1,
        "type": 2,
        "enum": 3,
        "minimum": 4,
        "maximum": 5,
        "minLength": 6,
        "maxLength": 7,
        "minItems": 8,
        "maxItems": 9,
    }.get(str(error.validator), 100)
    return priority, _path_text(tuple(error.path)), error.message


def _instance_at_path(arguments: Mapping[str, Any], path: tuple[Any, ...]) -> object:
    current: object = arguments
    for part in path:
        if isinstance(current, Mapping) and isinstance(part, str):
            current = current.get(part)
        elif isinstance(current, list) and isinstance(part, int):
            current = current[part]
        else:
            return None
    return current


def _path_text(path: tuple[Any, ...]) -> str:
    parts: list[str] = []
    for item in path:
        if isinstance(item, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{item}]"
            else:
                parts.append(f"[{item}]")
        else:
            parts.append(str(item))
    return ".".join(parts)


def _type_text(value: object) -> str:
    types = _type_names(value)
    if len(types) == 1:
        return types[0]
    return "one of " + ", ".join(types)


def _type_names(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return (str(value),)


def _enum_text(value: object) -> str:
    if not isinstance(value, list):
        return repr(value)
    return ", ".join(repr(item) for item in value)


def _number_text(value: object) -> str:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return f"{value:g}"
    return str(value)


def _plain_json_object(values: Mapping[str, JsonValue]) -> dict[str, Any]:
    return {key: _plain_json_value(value) for key, value in values.items()}


def _plain_json_value(value: JsonValue) -> Any:
    if isinstance(value, Mapping):
        return _plain_json_object(cast(Mapping[str, JsonValue], value))
    if isinstance(value, list):
        return [_plain_json_value(item) for item in value]
    return value
