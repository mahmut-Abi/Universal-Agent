from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from universal_agent.core import (
    DomainIdentity,
    ErrorCode,
    JsonMapping,
    ObservationStatus,
    ToolCall,
    ToolDefinition,
    ToolResult,
    immutable_json,
)


class Tool(Protocol):
    @property
    def definition(self) -> ToolDefinition: ...

    async def execute(self, arguments: JsonMapping) -> JsonMapping: ...


class DuplicateToolError(ValueError):
    pass


class UnknownToolError(LookupError):
    pass


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
            item
            for item in self._tools.values()
            if capability in item.tool.definition.capabilities
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
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

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
        if identity is not None and (call.domain_name or call.domain_version) and (
            call.domain_name != identity.name or call.domain_version != identity.version
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
        missing = [
            name for name in tool.definition.required_arguments if name not in call.arguments
        ]
        if missing:
            return ToolResult(
                status=ObservationStatus.FAILED,
                error=f"missing required arguments: {', '.join(missing)}",
                error_code=ErrorCode.VALIDATION_ERROR,
            )
        try:
            output = await asyncio.wait_for(
                tool.execute(call.arguments),
                timeout=tool.definition.timeout_seconds,
            )
        except TimeoutError:
            return ToolResult(
                status=ObservationStatus.TIMED_OUT,
                error=f"tool timed out: {call.tool_name}",
                error_code=ErrorCode.TIMEOUT,
            )
        except Exception as exc:
            return ToolResult(
                status=ObservationStatus.FAILED,
                error=f"tool failed: {exc}",
                error_code=ErrorCode.TOOL_FAILURE,
            )
        return ToolResult(status=ObservationStatus.SUCCEEDED, output=immutable_json(output))
