from __future__ import annotations

from dataclasses import dataclass

from universal_agent.core import (
    CapabilityCategory,
    EventId,
    Goal,
    RiskLevel,
    SessionId,
    SideEffect,
    Task,
)
from universal_agent.domain import ActiveDomain, RuntimeComponents
from universal_agent.profile import AgentProfile, ProfileRegistry
from universal_agent.runtime import (
    RuntimeAPI,
    RuntimeEventBatch,
    RuntimeEventView,
    RuntimeRun,
    SessionView,
)


@dataclass(frozen=True, slots=True)
class HealthView:
    status: str
    service: str


@dataclass(frozen=True, slots=True)
class ReadyView:
    ready: bool
    reason: str
    domain_count: int
    capability_count: int
    tool_count: int


@dataclass(frozen=True, slots=True)
class DomainView:
    name: str
    version: str
    description: str
    primary: bool
    ontology: tuple[str, ...]
    capability_names: tuple[str, ...]
    evaluator_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CapabilityView:
    name: str
    description: str
    category: CapabilityCategory
    risk: RiskLevel
    domain_name: str
    domain_version: str
    tool_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ToolView:
    name: str
    description: str
    capabilities: tuple[str, ...]
    required_arguments: tuple[str, ...]
    side_effect: SideEffect
    risk: RiskLevel
    timeout_seconds: float
    priority: int
    domain_name: str
    domain_version: str


@dataclass(frozen=True, slots=True)
class ProfileView:
    name: str
    version: str
    description: str
    domain_name: str
    domain_version: str


class RuntimeService:
    """Application-facing service module for future agentd adapters.

    Execution and lifecycle control flow through RuntimeAPI. This service adds
    product-level health, readiness and catalog metadata over RuntimeComponents.
    """

    def __init__(
        self,
        *,
        runtime_api: RuntimeAPI,
        components: RuntimeComponents,
        profiles: tuple[AgentProfile, ...] = (),
    ) -> None:
        self._runtime_api = runtime_api
        self._components = components
        self._profiles = ProfileRegistry(profiles)

    def health(self) -> HealthView:
        return HealthView(status="ok", service="universal-agent-runtime")

    def ready(self) -> ReadyView:
        domains = self._components.domain_composition.domains
        capabilities = self._components.capabilities.all()
        tools = self._components.tools.all()
        missing_tools = tuple(
            capability.name
            for capability in capabilities
            if not self._components.tools.registrations_for_capability(capability.name)
        )
        ready = bool(domains) and bool(capabilities) and bool(tools) and not missing_tools
        reason = (
            "ready"
            if ready
            else _not_ready_reason(
                has_domains=bool(domains),
                has_capabilities=bool(capabilities),
                has_tools=bool(tools),
                missing_tools=missing_tools,
            )
        )
        return ReadyView(
            ready=ready,
            reason=reason,
            domain_count=len(domains),
            capability_count=len(capabilities),
            tool_count=len(tools),
        )

    def domains(self) -> tuple[DomainView, ...]:
        primary = self._components.domain_composition.primary.identity
        return tuple(
            domain_view(domain, primary=domain.identity == primary)
            for domain in self._components.domain_composition.domains
        )

    def capabilities(self) -> tuple[CapabilityView, ...]:
        views: list[CapabilityView] = []
        for domain in self._components.domain_composition.domains:
            for capability in domain.capabilities:
                tool_names = tuple(
                    registration.tool.definition.name
                    for registration in sorted(
                        self._components.tools.registrations_for_capability(capability.name),
                        key=lambda item: item.tool.definition.name,
                    )
                )
                views.append(
                    CapabilityView(
                        name=capability.name,
                        description=capability.description,
                        category=capability.category,
                        risk=capability.risk,
                        domain_name=domain.identity.name,
                        domain_version=domain.identity.version,
                        tool_names=tool_names,
                    )
                )
        return tuple(sorted(views, key=lambda item: item.name))

    def tools(self) -> tuple[ToolView, ...]:
        views: list[ToolView] = []
        for domain in self._components.domain_composition.domains:
            for tool in domain.tools:
                definition = tool.definition
                views.append(
                    ToolView(
                        name=definition.name,
                        description=definition.description,
                        capabilities=definition.capabilities,
                        required_arguments=definition.required_arguments,
                        side_effect=definition.side_effect,
                        risk=definition.risk,
                        timeout_seconds=definition.timeout_seconds,
                        priority=definition.priority,
                        domain_name=domain.identity.name,
                        domain_version=domain.identity.version,
                    )
                )
        return tuple(sorted(views, key=lambda item: item.name))

    def profiles(self) -> tuple[ProfileView, ...]:
        return tuple(profile_view(profile) for profile in self._profiles.all())

    def accepts_profile(self, name: str) -> bool:
        return self._profiles.has(name)

    async def run_goal(self, goal: Goal, task: Task) -> RuntimeRun:
        return await self._runtime_api.run_goal(goal, task)

    async def resume_session(
        self,
        session_id: SessionId,
        *,
        confirmed: bool | None = None,
    ) -> RuntimeRun:
        return await self._runtime_api.resume_session(session_id, confirmed=confirmed)

    async def pause_session(
        self,
        session_id: SessionId,
        *,
        reason: str = "session paused",
    ) -> RuntimeRun:
        return await self._runtime_api.pause_session(session_id, reason=reason)

    async def cancel_session(
        self,
        session_id: SessionId,
        *,
        reason: str = "session cancelled",
    ) -> RuntimeRun:
        return await self._runtime_api.cancel_session(session_id, reason=reason)

    async def get_session(self, session_id: SessionId) -> SessionView:
        return await self._runtime_api.get_session(session_id)

    async def list_events(self, session_id: SessionId) -> tuple[RuntimeEventView, ...]:
        return await self._runtime_api.list_events(session_id)

    async def stream_events(
        self,
        session_id: SessionId,
        *,
        after_event_id: EventId | None = None,
        limit: int | None = None,
    ) -> RuntimeEventBatch:
        return await self._runtime_api.stream_events(
            session_id,
            after_event_id=after_event_id,
            limit=limit,
        )


def domain_view(domain: ActiveDomain, *, primary: bool) -> DomainView:
    metadata = domain.manifest.metadata
    return DomainView(
        name=metadata.name,
        version=metadata.version,
        description=metadata.description,
        primary=primary,
        ontology=domain.manifest.ontology,
        capability_names=domain.manifest.capability_names,
        evaluator_names=domain.manifest.evaluator_names,
    )


def profile_view(profile: AgentProfile) -> ProfileView:
    assert profile.domain.name is not None
    assert profile.domain.version is not None
    return ProfileView(
        name=profile.name,
        version=profile.version,
        description=profile.description,
        domain_name=profile.domain.name,
        domain_version=profile.domain.version,
    )


def _not_ready_reason(
    *,
    has_domains: bool,
    has_capabilities: bool,
    has_tools: bool,
    missing_tools: tuple[str, ...],
) -> str:
    if not has_domains:
        return "no domains loaded"
    if not has_capabilities:
        return "no capabilities registered"
    if not has_tools:
        return "no tools registered"
    if missing_tools:
        return "capabilities without tools: " + ", ".join(missing_tools)
    return "not ready"
