from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints, TypeAdapter
from pydantic import JsonValue as PydanticJsonValue
from pydantic import ValidationError as PydanticValidationError

from universal_agent.core.models import JsonMapping, JsonValue

__all__ = [
    "ConfigPayload",
    "PydanticErrorDetails",
    "PydanticJsonValue",
    "enum_value",
    "json_mapping",
    "parse_bool",
    "parse_int",
    "parse_json_object",
    "parse_json_object_sequence",
    "parse_json_value",
    "parse_lower_sha256_hex_digest",
    "parse_non_empty_string",
    "parse_non_empty_string_sequence",
    "parse_optional_bool",
    "parse_optional_int",
    "parse_optional_lower_sha256_hex_digest",
    "parse_optional_non_empty_string",
    "parse_optional_string",
    "parse_payload",
    "parse_string",
    "parse_string_sequence",
    "pydantic_error_details",
    "pydantic_error_message",
    "string_mapping",
]


class ConfigPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


@dataclass(frozen=True, slots=True)
class PydanticErrorDetails:
    path: str
    error_type: str
    message: str


def _non_empty_string_value(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be empty")
    return value


_PydanticNonEmptyString = Annotated[str, AfterValidator(_non_empty_string_value)]
_PydanticLowerSha256HexDigest = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]


_JSON_OBJECT_ADAPTER: TypeAdapter[dict[str, PydanticJsonValue]] = TypeAdapter(
    dict[str, PydanticJsonValue]
)
_JSON_OBJECT_SEQUENCE_ADAPTER: TypeAdapter[list[dict[str, PydanticJsonValue]]] = TypeAdapter(
    list[dict[str, PydanticJsonValue]]
)
_JSON_VALUE_ADAPTER: TypeAdapter[PydanticJsonValue] = TypeAdapter(PydanticJsonValue)
_BOOL_ADAPTER: TypeAdapter[bool] = TypeAdapter(bool)
_INT_ADAPTER: TypeAdapter[int] = TypeAdapter(int)
_STRING_ADAPTER: TypeAdapter[str] = TypeAdapter(str)
_NON_EMPTY_STRING_ADAPTER: TypeAdapter[_PydanticNonEmptyString] = TypeAdapter(
    _PydanticNonEmptyString
)
_NON_EMPTY_STRING_SEQUENCE_ADAPTER: TypeAdapter[list[_PydanticNonEmptyString]] = TypeAdapter(
    list[_PydanticNonEmptyString]
)
_STRING_SEQUENCE_ADAPTER: TypeAdapter[list[str]] = TypeAdapter(list[str])
_STRING_MAPPING_ADAPTER: TypeAdapter[dict[str, str]] = TypeAdapter(dict[str, str])
_LOWER_SHA256_HEX_DIGEST_ADAPTER: TypeAdapter[_PydanticLowerSha256HexDigest] = TypeAdapter(
    _PydanticLowerSha256HexDigest
)


def parse_payload[T: BaseModel](
    model_type: type[T],
    values: Mapping[str, JsonValue],
    *,
    field: str | None = None,
    missing_template: str | None = None,
    expected_types: Mapping[str, str] | None = None,
) -> T:
    try:
        return model_type.model_validate(dict(values))
    except PydanticValidationError as exc:
        raise ValueError(
            pydantic_error_message(
                exc,
                field,
                missing_template=missing_template,
                expected_types=expected_types,
            )
        ) from exc


def parse_json_object(value: object, field: str) -> JsonMapping:
    values: object = dict(value) if isinstance(value, Mapping) else value
    try:
        parsed = _JSON_OBJECT_ADAPTER.validate_python(values, strict=True)
    except PydanticValidationError as exc:
        raise ValueError(pydantic_error_message(exc, field)) from exc
    return json_mapping(parsed)


def parse_json_object_sequence(value: object, field: str) -> tuple[JsonMapping, ...]:
    if value is None:
        return ()
    values: object = (
        [dict(item) if isinstance(item, Mapping) else item for item in value]
        if isinstance(value, list)
        else value
    )
    try:
        parsed = _JSON_OBJECT_SEQUENCE_ADAPTER.validate_python(values, strict=True)
    except PydanticValidationError as exc:
        raise ValueError(pydantic_error_message(exc, field)) from exc
    return tuple(json_mapping(item) for item in parsed)


def parse_json_value(value: object, field: str) -> JsonValue:
    try:
        return _JSON_VALUE_ADAPTER.validate_python(value, strict=True)
    except PydanticValidationError as exc:
        raise ValueError(pydantic_error_message(exc, field)) from exc


def parse_string(value: object, field: str, *, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    try:
        return _STRING_ADAPTER.validate_python(value, strict=True)
    except PydanticValidationError as exc:
        raise ValueError(pydantic_error_message(exc, field)) from exc


def parse_optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return parse_string(value, field)


def parse_non_empty_string(
    value: object,
    field: str,
    *,
    empty_template: str = "{path} must not be empty",
) -> str:
    try:
        return _NON_EMPTY_STRING_ADAPTER.validate_python(value, strict=True)
    except PydanticValidationError as exc:
        details = pydantic_error_details(exc, field)
        if details.error_type == "value_error" and details.message.endswith(
            "must not be empty"
        ):
            raise ValueError(empty_template.format(path=details.path)) from exc
        raise ValueError(pydantic_error_message(exc, field)) from exc


def parse_optional_non_empty_string(
    value: object,
    field: str,
    *,
    empty_template: str = "{path} must not be empty",
) -> str | None:
    if value is None:
        return None
    return parse_non_empty_string(value, field, empty_template=empty_template)


def parse_lower_sha256_hex_digest(value: object, field: str) -> str:
    try:
        return _LOWER_SHA256_HEX_DIGEST_ADAPTER.validate_python(value, strict=True)
    except PydanticValidationError as exc:
        details = pydantic_error_details(exc, field)
        if details.error_type == "string_type":
            raise ValueError(f"{details.path} must be a string") from exc
        raise ValueError(f"{details.path} must be a lowercase SHA-256 hex digest") from exc


def parse_optional_lower_sha256_hex_digest(value: object, field: str) -> str:
    if value is None or value == "":
        return ""
    return parse_lower_sha256_hex_digest(value, field)


def parse_string_sequence(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    try:
        parsed = _STRING_SEQUENCE_ADAPTER.validate_python(_sequence_input(value), strict=True)
    except PydanticValidationError as exc:
        raise ValueError(pydantic_error_message(exc, field)) from exc
    return tuple(parsed)


def parse_non_empty_string_sequence(
    value: object,
    field: str,
    *,
    empty_template: str = "{path} must not be empty",
    item_type_template: str | None = None,
) -> tuple[str, ...]:
    if value is None:
        return ()
    try:
        parsed = _NON_EMPTY_STRING_SEQUENCE_ADAPTER.validate_python(
            _sequence_input(value),
            strict=True,
        )
    except PydanticValidationError as exc:
        details = pydantic_error_details(exc, field)
        if details.error_type == "value_error" and details.message.endswith(
            "must not be empty"
        ):
            raise ValueError(empty_template.format(path=details.path)) from exc
        if details.error_type == "string_type" and item_type_template is not None:
            raise ValueError(item_type_template.format(path=details.path)) from exc
        raise ValueError(pydantic_error_message(exc, field)) from exc
    return tuple(parsed)


def _sequence_input(value: object) -> object:
    if isinstance(value, tuple):
        return list(value)
    return value


def parse_bool(value: object, field: str) -> bool:
    try:
        return _BOOL_ADAPTER.validate_python(value, strict=True)
    except PydanticValidationError as exc:
        raise ValueError(pydantic_error_message(exc, field)) from exc


def parse_optional_bool(value: object, field: str) -> bool | None:
    if value is None:
        return None
    return parse_bool(value, field)


def parse_int(value: object, field: str, *, default: int | None = None) -> int:
    if value is None and default is not None:
        return default
    try:
        return _INT_ADAPTER.validate_python(value, strict=True)
    except PydanticValidationError as exc:
        raise ValueError(pydantic_error_message(exc, field)) from exc


def parse_optional_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    return parse_int(value, field)


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
    details = pydantic_error_details(error, field)
    path = details.path
    error_type = details.error_type
    if not error_type:
        return details.message
    if error_type == "value_error":
        return details.message.removeprefix("Value error, ")
    if error_type == "missing" and missing_template is not None:
        return missing_template.format(path=path)
    expected = _expected_error_type(error_type, path, expected_types)
    if expected is not None:
        return f"{path} must be {expected}"
    if details.message:
        return f"{path}: {details.message}" if path else details.message
    return str(error)


def pydantic_error_details(
    error: PydanticValidationError,
    field: str | None = None,
) -> PydanticErrorDetails:
    errors = error.errors(include_url=False)
    if not errors:
        return PydanticErrorDetails("", "", str(error))
    first = errors[0]
    return PydanticErrorDetails(
        path=_pydantic_error_path(first.get("loc", ()), field),
        error_type=str(first.get("type", "")),
        message=str(first.get("msg", "")),
    )


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
