from __future__ import annotations

from dataclasses import dataclass

from universal_agent.core import CapabilityDefinition, DomainIdentity
from universal_agent.core.config_validation import parse_non_empty_string
from universal_agent.tools import Tool, ToolRegistration, ToolRegistry


class DuplicateCapabilityError(ValueError):
    pass


class UnknownCapabilityError(LookupError):
    pass


class CapabilityUnavailableError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class CapabilityRegistration:
    definition: CapabilityDefinition
    domain_identity: DomainIdentity | None = None


@dataclass(frozen=True, slots=True)
class CapabilityResolution:
    capability: CapabilityDefinition
    tool: Tool
    capability_domain: DomainIdentity | None = None
    tool_domain: DomainIdentity | None = None


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilityRegistration] = {}

    def register(
        self,
        capability: CapabilityDefinition,
        domain_identity: DomainIdentity | None = None,
    ) -> None:
        parse_non_empty_string(capability.name, "capability name")
        if capability.name in self._capabilities:
            raise DuplicateCapabilityError(f"capability already registered: {capability.name}")
        self._capabilities[capability.name] = CapabilityRegistration(
            capability,
            domain_identity,
        )

    def resolve(self, name: str) -> CapabilityDefinition:
        return self.resolve_registration(name).definition

    def resolve_registration(self, name: str) -> CapabilityRegistration:
        try:
            return self._capabilities[name]
        except KeyError as exc:
            raise UnknownCapabilityError(f"unknown capability: {name}") from exc

    def all(self) -> tuple[CapabilityDefinition, ...]:
        return tuple(
            item.definition
            for item in sorted(
                self._capabilities.values(),
                key=lambda item: item.definition.name,
            )
        )


class CapabilityResolver:
    def __init__(self, capabilities: CapabilityRegistry, tools: ToolRegistry) -> None:
        self._capabilities = capabilities
        self._tools = tools

    def resolve(self, name: str) -> tuple[CapabilityDefinition, Tool]:
        resolution = self.resolve_registration(name)
        return resolution.capability, resolution.tool

    def resolve_registration(self, name: str) -> CapabilityResolution:
        capability = self._capabilities.resolve_registration(name)
        candidates = self._tools.registrations_for_capability(name)
        if not candidates:
            raise CapabilityUnavailableError(f"no tool implements capability: {name}")
        tool = min(
            candidates,
            key=lambda item: (item.tool.definition.priority, item.tool.definition.name),
        )
        self._validate_domain_match(capability, tool)
        return CapabilityResolution(
            capability.definition,
            tool.tool,
            capability.domain_identity,
            tool.domain_identity,
        )

    def _validate_domain_match(
        self,
        capability: CapabilityRegistration,
        tool: ToolRegistration,
    ) -> None:
        if capability.domain_identity is None or tool.domain_identity is None:
            return
        if capability.domain_identity != tool.domain_identity:
            capability_identity = capability.domain_identity
            tool_identity = tool.domain_identity
            raise CapabilityUnavailableError(
                "capability tool domain mismatch: "
                f"{capability.definition.name} belongs to "
                f"{capability_identity.name}@{capability_identity.version}, "
                f"but tool {tool.tool.definition.name} belongs to "
                f"{tool_identity.name}@{tool_identity.version}"
            )
