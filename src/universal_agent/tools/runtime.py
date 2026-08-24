from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from universal_agent.core import (
    DomainIdentity,
    ErrorCode,
    JsonMapping,
    JsonValue,
    ObservationStatus,
    ToolCall,
    ToolDefinition,
    ToolResult,
    immutable_json,
)
from universal_agent.security import (
    EnvSecretProvider,
    SecretProvider,
    SecretResolutionError,
    SecretResolutionReport,
    resolve_secret_arguments,
)


class Tool(Protocol):
    @property
    def definition(self) -> ToolDefinition: ...

    async def execute(self, arguments: JsonMapping) -> JsonMapping: ...


class DuplicateToolError(ValueError):
    pass


class UnknownToolError(LookupError):
    pass


class UncertainToolExecutionError(RuntimeError):
    """Raised by tools when the external action outcome cannot be known."""


@dataclass(frozen=True, slots=True)
class ToolRegistration:
    tool: Tool
    domain_identity: DomainIdentity | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolRegistration] = {}

    def register(self, tool: Tool, domain_identity: DomainIdentity | None = None) -> None:
        name = tool.definition.name
        if not name.strip():
            raise ValueError("tool name must not be empty")
        if not tool.definition.capabilities:
            raise ValueError(f"tool must implement at least one capability: {name}")
        if name in self._tools:
            raise DuplicateToolError(f"tool already registered: {name}")
        self._tools[name] = ToolRegistration(tool, domain_identity)

    def resolve(self, name: str) -> Tool:
        return self.resolve_registration(name).tool

    def resolve_registration(self, name: str) -> ToolRegistration:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise UnknownToolError(f"unknown tool: {name}") from exc

    def for_capability(self, capability: str) -> tuple[Tool, ...]:
        return tuple(item.tool for item in self.registrations_for_capability(capability))

    def registrations_for_capability(self, capability: str) -> tuple[ToolRegistration, ...]:
        return tuple(
            item for item in self._tools.values() if capability in item.tool.definition.capabilities
        )

    def all(self) -> tuple[Tool, ...]:
        return tuple(
            item.tool
            for item in sorted(
                self._tools.values(),
                key=lambda item: item.tool.definition.name,
            )
        )


class ToolRuntime:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        secret_provider: SecretProvider | None = None,
        secret_resolution: SecretResolutionReport | None = None,
    ) -> None:
        self._registry = registry
        self._secret_provider = secret_provider or EnvSecretProvider()
        self._secret_resolution = secret_resolution

    async def execute(self, call: ToolCall) -> ToolResult:
        try:
            registration = self._registry.resolve_registration(call.tool_name)
        except UnknownToolError as exc:
            return ToolResult(
                status=ObservationStatus.FAILED,
                error=str(exc),
                error_code=ErrorCode.UNKNOWN_TOOL,
            )
        tool = registration.tool
        identity = registration.domain_identity
        if (
            identity is not None
            and (call.domain_name or call.domain_version)
            and (call.domain_name != identity.name or call.domain_version != identity.version)
        ):
            return ToolResult(
                status=ObservationStatus.FAILED,
                error=(
                    "tool domain mismatch: "
                    f"{call.domain_name}@{call.domain_version} != "
                    f"{identity.name}@{identity.version}"
                ),
                error_code=ErrorCode.VALIDATION_ERROR,
            )
        if call.capability not in tool.definition.capabilities:
            return ToolResult(
                status=ObservationStatus.FAILED,
                error=f"tool does not implement capability: {call.capability}",
                error_code=ErrorCode.VALIDATION_ERROR,
            )
        try:
            arguments = resolve_secret_arguments(
                call.arguments,
                provider=self._secret_provider,
                resolution=self._secret_resolution,
            )
        except SecretResolutionError as exc:
            return ToolResult(
                status=ObservationStatus.FAILED,
                error=str(exc),
                error_code=ErrorCode.VALIDATION_ERROR,
            )
        validation_error = validate_tool_arguments(tool.definition, arguments)
        if validation_error is not None:
            return ToolResult(
                status=ObservationStatus.FAILED,
                error=validation_error,
                error_code=ErrorCode.VALIDATION_ERROR,
            )
        try:
            output = await asyncio.wait_for(
                tool.execute(arguments),
                timeout=tool.definition.timeout_seconds,
            )
        except TimeoutError:
            return ToolResult(
                status=ObservationStatus.TIMED_OUT,
                error=f"tool timed out: {call.tool_name}",
                error_code=ErrorCode.TIMEOUT,
            )
        except UncertainToolExecutionError as exc:
            return ToolResult(
                status=ObservationStatus.UNKNOWN,
                error=f"tool outcome unknown: {exc}",
                error_code=ErrorCode.UNKNOWN_EXECUTION,
            )
        except Exception as exc:
            return ToolResult(
                status=ObservationStatus.FAILED,
                error=f"tool failed: {exc}",
                error_code=ErrorCode.TOOL_FAILURE,
            )
        return ToolResult(status=ObservationStatus.SUCCEEDED, output=immutable_json(output))


class _ArgumentSchemaError(ValueError):
    pass


def validate_tool_arguments(
    definition: ToolDefinition,
    arguments: JsonMapping,
) -> str | None:
    """Validate a ToolCall argument payload against the deterministic tool contract."""

    missing = [name for name in definition.required_arguments if name not in arguments]
    if missing:
        return f"missing required arguments: {', '.join(missing)}"
    if not definition.argument_schema:
        return None
    try:
        return _validate_argument_schema(definition.argument_schema, arguments)
    except _ArgumentSchemaError as exc:
        return str(exc)


def _validate_argument_schema(schema: JsonMapping, arguments: JsonMapping) -> str | None:
    required = _string_list(schema.get("required", []), "argument_schema.required")
    missing = [name for name in required if name not in arguments]
    if missing:
        return f"missing required arguments: {', '.join(missing)}"

    properties = _object(schema.get("properties", {}), "argument_schema.properties")
    additional = schema.get("additionalProperties", True)
    if not isinstance(additional, bool):
        raise _ArgumentSchemaError("argument_schema.additionalProperties must be a boolean")
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
            raise _ArgumentSchemaError(
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


def _object(value: JsonValue, field_name: str) -> JsonMapping:
    if not isinstance(value, Mapping):
        raise _ArgumentSchemaError(f"{field_name} must be an object")
    result: dict[str, JsonValue] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise _ArgumentSchemaError(f"{field_name} keys must be strings")
        result[key] = item
    return immutable_json(result)


def _list(value: JsonValue, field_name: str) -> tuple[JsonValue, ...]:
    if not isinstance(value, list):
        raise _ArgumentSchemaError(f"{field_name} must be a list")
    return tuple(value)


def _string_list(value: JsonValue, field_name: str) -> tuple[str, ...]:
    items = _list(value, field_name)
    strings: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise _ArgumentSchemaError(f"{field_name} must be a list of strings")
        strings.append(item)
    return tuple(strings)


def _type_names(value: JsonValue, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return _string_list(value, field_name)


def _integer(value: JsonValue, field_name: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise _ArgumentSchemaError(f"{field_name} must be an integer")


def _number(value: JsonValue, field_name: str) -> int | float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value
    raise _ArgumentSchemaError(f"{field_name} must be a number")


def _is_number(value: JsonValue) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _type_text(types: tuple[str, ...]) -> str:
    if len(types) == 1:
        return types[0]
    return "one of " + ", ".join(types)


def _enum_text(value: JsonValue) -> str:
    items = _list(value, "enum")
    return ", ".join(repr(item) for item in items)
