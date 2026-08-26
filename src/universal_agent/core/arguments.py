from __future__ import annotations

from collections.abc import Mapping

from universal_agent.core.models import JsonMapping, JsonValue, immutable_json


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
        return _validate_argument_schema(argument_schema, arguments)
    except ArgumentSchemaError as exc:
        return str(exc)


def _validate_argument_schema(schema: JsonMapping, arguments: JsonMapping) -> str | None:
    required = _string_list(schema.get("required", []), "argument_schema.required")
    missing = [name for name in required if name not in arguments]
    if missing:
        return f"missing required arguments: {', '.join(missing)}"

    properties = _object(schema.get("properties", {}), "argument_schema.properties")
    additional = schema.get("additionalProperties", True)
    if not isinstance(additional, bool):
        raise ArgumentSchemaError("argument_schema.additionalProperties must be a boolean")
    if not additional:
        unknown = tuple(name for name in arguments if name not in properties)
        if unknown:
            return f"unexpected arguments: {', '.join(sorted(unknown))}"

    for name, value in arguments.items():
        raw_spec = properties.get(name)
        if raw_spec is None:
            continue
        spec = _object(raw_spec, f"argument_schema.properties.{name}")
        error = _validate_argument_value(name, value, spec)
        if error is not None:
            return error
    return None


def _validate_argument_value(name: str, value: JsonValue, spec: JsonMapping) -> str | None:
    raw_types = spec.get("type")
    if raw_types is not None:
        types = _type_names(raw_types, f"argument_schema.properties.{name}.type")
        unsupported = tuple(item for item in types if item not in _SUPPORTED_TYPES)
        if unsupported:
            raise ArgumentSchemaError(
                f"argument_schema.properties.{name}.type unsupported: {', '.join(unsupported)}"
            )
        if not any(_matches_type(value, item) for item in types):
            return f"argument {name} must be {_type_text(types)}"

    enum = spec.get("enum")
    if enum is not None and value not in _list(enum, f"argument_schema.properties.{name}.enum"):
        return f"argument {name} must be one of {_enum_text(enum)}"

    if _is_number(value):
        numeric_value = _number(value, f"argument_schema.properties.{name}")
        minimum = spec.get("minimum")
        maximum = spec.get("maximum")
        if minimum is not None:
            minimum_value = _number(minimum, f"argument_schema.properties.{name}.minimum")
            if numeric_value < minimum_value:
                return f"argument {name} must be >= {minimum_value:g}"
        if maximum is not None:
            maximum_value = _number(maximum, f"argument_schema.properties.{name}.maximum")
            if numeric_value > maximum_value:
                return f"argument {name} must be <= {maximum_value:g}"

    if isinstance(value, str):
        min_length = spec.get("minLength")
        max_length = spec.get("maxLength")
        if min_length is not None:
            minimum_length = _integer(min_length, f"argument_schema.properties.{name}.minLength")
            if len(value) < minimum_length:
                return f"argument {name} length must be >= {minimum_length}"
        if max_length is not None:
            maximum_length = _integer(max_length, f"argument_schema.properties.{name}.maxLength")
            if len(value) > maximum_length:
                return f"argument {name} length must be <= {maximum_length}"

    object_error = _validate_object_constraints(name, value, spec)
    if object_error is not None:
        return object_error

    array_error = _validate_array_constraints(name, value, spec)
    if array_error is not None:
        return array_error
    return None


_SUPPORTED_TYPES = frozenset({"string", "integer", "number", "boolean", "object", "array", "null"})


def _matches_type(value: JsonValue, type_name: str) -> bool:
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return _is_number(value)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "object":
        return isinstance(value, Mapping)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "null":
        return value is None
    return False


def _validate_object_constraints(
    name: str,
    value: JsonValue,
    spec: JsonMapping,
) -> str | None:
    has_object_constraints = (
        "required" in spec or "properties" in spec or "additionalProperties" in spec
    )
    if not has_object_constraints:
        return None
    if _is_nullable_value(value, spec):
        return None
    if not isinstance(value, Mapping):
        return f"argument {name} must be object"

    required = _string_list(
        spec.get("required", []),
        f"argument_schema.properties.{name}.required",
    )
    missing = [property_name for property_name in required if property_name not in value]
    if missing:
        return f"argument {name} missing required properties: {', '.join(missing)}"

    properties = _object(
        spec.get("properties", {}),
        f"argument_schema.properties.{name}.properties",
    )
    additional = spec.get("additionalProperties", True)
    if not isinstance(additional, bool):
        raise ArgumentSchemaError(
            f"argument_schema.properties.{name}.additionalProperties must be a boolean"
        )
    if not additional:
        unknown = tuple(property_name for property_name in value if property_name not in properties)
        if unknown:
            return f"argument {name} has unexpected properties: {', '.join(sorted(unknown))}"

    for property_name, property_value in value.items():
        raw_spec = properties.get(property_name)
        if raw_spec is None:
            continue
        property_spec = _object(
            raw_spec,
            f"argument_schema.properties.{name}.properties.{property_name}",
        )
        error = _validate_argument_value(f"{name}.{property_name}", property_value, property_spec)
        if error is not None:
            return error
    return None


def _validate_array_constraints(
    name: str,
    value: JsonValue,
    spec: JsonMapping,
) -> str | None:
    has_array_constraints = "items" in spec or "minItems" in spec or "maxItems" in spec
    if not has_array_constraints:
        return None
    if _is_nullable_value(value, spec):
        return None
    if not isinstance(value, list):
        return f"argument {name} must be array"

    min_items = spec.get("minItems")
    if min_items is not None:
        minimum = _integer(min_items, f"argument_schema.properties.{name}.minItems")
        if len(value) < minimum:
            return f"argument {name} length must be >= {minimum}"
    max_items = spec.get("maxItems")
    if max_items is not None:
        maximum = _integer(max_items, f"argument_schema.properties.{name}.maxItems")
        if len(value) > maximum:
            return f"argument {name} length must be <= {maximum}"

    raw_items = spec.get("items")
    if raw_items is None:
        return None
    item_spec = _object(raw_items, f"argument_schema.properties.{name}.items")
    for index, item in enumerate(value):
        error = _validate_argument_value(f"{name}[{index}]", item, item_spec)
        if error is not None:
            return error
    return None


def _is_nullable_value(value: JsonValue, spec: JsonMapping) -> bool:
    if value is not None:
        return False
    raw_types = spec.get("type")
    if raw_types is None:
        return False
    return "null" in _type_names(raw_types, "type")


def _object(value: JsonValue, field_name: str) -> JsonMapping:
    if not isinstance(value, Mapping):
        raise ArgumentSchemaError(f"{field_name} must be an object")
    result: dict[str, JsonValue] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ArgumentSchemaError(f"{field_name} keys must be strings")
        result[key] = item
    return immutable_json(result)


def _list(value: JsonValue, field_name: str) -> tuple[JsonValue, ...]:
    if not isinstance(value, list):
        raise ArgumentSchemaError(f"{field_name} must be a list")
    return tuple(value)


def _string_list(value: JsonValue, field_name: str) -> tuple[str, ...]:
    items = _list(value, field_name)
    strings: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ArgumentSchemaError(f"{field_name} must be a list of strings")
        strings.append(item)
    return tuple(strings)


def _type_names(value: JsonValue, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return _string_list(value, field_name)


def _integer(value: JsonValue, field_name: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ArgumentSchemaError(f"{field_name} must be an integer")


def _number(value: JsonValue, field_name: str) -> int | float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value
    raise ArgumentSchemaError(f"{field_name} must be a number")


def _is_number(value: JsonValue) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _type_text(types: tuple[str, ...]) -> str:
    if len(types) == 1:
        return types[0]
    return "one of " + ", ".join(types)


def _enum_text(value: JsonValue) -> str:
    items = _list(value, "enum")
    return ", ".join(repr(item) for item in items)
