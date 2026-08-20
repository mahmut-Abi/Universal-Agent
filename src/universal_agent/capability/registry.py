from __future__ import annotations

from universal_agent.core import CapabilityDefinition
from universal_agent.tools import Tool, ToolRegistry


class DuplicateCapabilityError(ValueError):
    pass


class UnknownCapabilityError(LookupError):
    pass


class CapabilityUnavailableError(LookupError):
    pass


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilityDefinition] = {}

    def register(self, capability: CapabilityDefinition) -> None:
        if not capability.name.strip():
            raise ValueError("capability name must not be empty")
        if capability.name in self._capabilities:
            raise DuplicateCapabilityError(f"capability already registered: {capability.name}")
        self._capabilities[capability.name] = capability

    def resolve(self, name: str) -> CapabilityDefinition:
        try:
            return self._capabilities[name]
        except KeyError as exc:
            raise UnknownCapabilityError(f"unknown capability: {name}") from exc

    def all(self) -> tuple[CapabilityDefinition, ...]:
        return tuple(sorted(self._capabilities.values(), key=lambda item: item.name))


class CapabilityResolver:
    def __init__(self, capabilities: CapabilityRegistry, tools: ToolRegistry) -> None:
        self._capabilities = capabilities
        self._tools = tools

    def resolve(self, name: str) -> tuple[CapabilityDefinition, Tool]:
        capability = self._capabilities.resolve(name)
        candidates = self._tools.for_capability(name)
        if not candidates:
            raise CapabilityUnavailableError(f"no tool implements capability: {name}")
        return capability, min(
            candidates, key=lambda tool: (tool.definition.priority, tool.definition.name)
        )
