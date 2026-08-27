from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, TypeAdapter
from pydantic import JsonValue as PydanticJsonValue
from pydantic import ValidationError as PydanticValidationError

from universal_agent.core.models import JsonMapping, JsonValue

__all__ = [
    "ConfigPayload",
    "PydanticJsonValue",
    "enum_value",
    "json_mapping",
    "parse_json_object",
    "parse_json_value",
    "parse_payload",
    "pydantic_error_message",
    "string_mapping",
]


class ConfigPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


_JSON_OBJECT_ADAPTER: TypeAdapter[dict[str, PydanticJsonValue]] = TypeAdapter(
    dict[str, PydanticJsonValue]
)
_JSON_VALUE_ADAPTER: TypeAdapter[PydanticJsonValue] = TypeAdapter(PydanticJsonValue)
_STRING_MAPPING_ADAPTER: TypeAdapter[dict[str, str]] = TypeAdapter(dict[str, str])


def parse_payload[T: BaseModel](
    model_type: type[T],
    values: Mapping[str, JsonValue],
    *,
    missing_template: str | None = None,
    expected_types: Mapping[str, str] | None = None,
) -> T:
    try:
        return model_type.model_validate(dict(values))
    except PydanticValidationError as exc:
        raise ValueError(
            pydantic_error_message(
                exc,
                missing_template=missing_template,
                expected_types=expected_types,
            )
        ) from exc


def parse_json_object(value: object, field: str) -> JsonMapping:
    try:
        parsed = _JSON_OBJECT_ADAPTER.validate_python(value, strict=True)
    except PydanticValidationError as exc:
        raise ValueError(pydantic_error_message(exc, field)) from exc
    return json_mapping(parsed)


def parse_json_value(value: object, field: str) -> JsonValue:
    try:
        return _JSON_VALUE_ADAPTER.validate_python(value, strict=True)
    except PydanticValidationError as exc:
        raise ValueError(pydantic_error_message(exc, field)) from exc


def json_mapping(value: Mapping[str, PydanticJsonValue]) -> JsonMapping:
    return value


def enum_value[T: StrEnum](enum_type: type[T], value: object, field: str) -> T:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        values = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{field} must be one of {values}") from exc


def string_mapping(value: object, field: str) -> Mapping[str, str]:
    values: object = dict(value) if isinstance(value, Mapping) else value
    try:
        return _STRING_MAPPING_ADAPTER.validate_python(values, strict=True)
    except PydanticValidationError as exc:
        raise ValueError(pydantic_error_message(exc, field)) from exc


def pydantic_error_message(
    error: PydanticValidationError,
    field: str | None = None,
    *,
    missing_template: str | None = None,
    expected_types: Mapping[str, str] | None = None,
) -> str:
    errors = error.errors(include_url=False)
    if not errors:
        return str(error)
    first = errors[0]
    path = _pydantic_error_path(first.get("loc", ()), field)
    error_type = str(first.get("type", ""))
    if error_type == "value_error":
        message = str(first.get("msg", ""))
        return message.removeprefix("Value error, ")
    if error_type == "missing" and missing_template is not None:
        return missing_template.format(path=path)
    expected = _expected_error_type(error_type, path, expected_types)
    if expected is not None:
        return f"{path} must be {expected}"
    message = str(first.get("msg", ""))
    if message:
        return f"{path}: {message}" if path else message
    return str(error)


def _pydantic_error_path(location: object, field: str | None) -> str:
    parts: list[str] = [field] if field else []
    if isinstance(location, tuple):
        for item in location:
            if isinstance(item, int):
                if parts:
                    parts[-1] = f"{parts[-1]}[{item}]"
                else:
                    parts.append(f"[{item}]")
            else:
                parts.append(str(item))
    return ".".join(part for part in parts if part)


def _expected_error_type(
    error_type: str,
    path: str,
    expected_types: Mapping[str, str] | None,
) -> str | None:
    if error_type == "missing":
        return _expected_missing_field(path)
    if expected_types is not None and error_type in expected_types:
        return expected_types[error_type]
    return {
        "bool_type": "a boolean",
        "dict_type": "an object",
        "float_type": "a number",
        "int_type": "an integer",
        "invalid-json-value": "JSON-compatible",
        "list_type": "a list",
        "model_attributes_type": "an object",
        "model_type": "an object",
        "string_type": "a string",
    }.get(error_type)


def _expected_missing_field(path: str) -> str:
    field_name = path.rsplit(".", maxsplit=1)[-1]
    return {
        "backend": "a string",
        "description": "a string",
        "key": "a string",
        "name": "a string",
        "provider": "a string",
        "required": "a boolean",
        "source": "a string",
        "version": "a string",
    }.get(field_name, "provided")
