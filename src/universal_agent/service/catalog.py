from __future__ import annotations

from universal_agent.capability import CapabilityUnavailableError, UnknownCapabilityError
from universal_agent.core import DomainIdentity, JsonMapping, immutable_json
from universal_agent.domain import DomainPackageRegistry, RuntimeComponents
from universal_agent.multi_agent import AgentDelegationState, AgentRegistry
from universal_agent.profile import ProfileRegistry
from universal_agent.service.projections import (
    domain_package_view,
    domain_view,
    evaluator_view,
    memory_view,
    multi_agent_instance_view,
    multi_agent_profile_view,
    policy_view,
    profile_view,
)
from universal_agent.service.views import (
    CapabilityView,
    DomainPackageView,
    DomainView,
    EvaluatorView,
    MemoryView,
    MultiAgentDelegationTaskView,
    MultiAgentView,
    PolicyView,
    ProfileView,
    ToolView,
)


def domain_views(components: RuntimeComponents) -> tuple[DomainView, ...]:
    primary = components.domain_composition.primary.identity
    return tuple(
        domain_view(domain, primary=domain.identity == primary)
        for domain in components.domain_composition.domains
    )


def domain_package_views(
    registry: DomainPackageRegistry,
    *,
    tag: str | None = None,
) -> tuple[DomainPackageView, ...]:
    return tuple(domain_package_view(package) for package in registry.list(tag=tag))


def domain_package_detail(
    registry: DomainPackageRegistry,
    name: str,
    version: str | None = None,
) -> DomainPackageView:
    package = (
        registry.get_by_name(name)
        if version is None
        else registry.get(DomainIdentity(name, version))
    )
    return domain_package_view(package)


def capability_views(components: RuntimeComponents) -> tuple[CapabilityView, ...]:
    views: list[CapabilityView] = []
    for domain in components.domain_composition.domains:
        for capability in domain.capabilities:
            tool_names = tuple(
                registration.tool.definition.name
                for registration in sorted(
                    components.tools.registrations_for_capability(capability.name),
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
                    required_arguments=_capability_required_arguments(components, capability.name),
                    argument_schema=_capability_argument_schema(components, capability.name),
                )
            )
    return tuple(sorted(views, key=lambda item: item.name))


def tool_views(components: RuntimeComponents) -> tuple[ToolView, ...]:
    views: list[ToolView] = []
    for domain in components.domain_composition.domains:
        for tool in domain.tools:
            definition = tool.definition
            views.append(
                ToolView(
                    name=definition.name,
                    description=definition.description,
                    capabilities=definition.capabilities,
                    required_arguments=definition.required_arguments,
                    argument_schema=definition.argument_schema,
                    side_effect=definition.side_effect,
                    risk=definition.risk,
                    timeout_seconds=definition.timeout_seconds,
                    priority=definition.priority,
                    domain_name=domain.identity.name,
                    domain_version=domain.identity.version,
                )
            )
    return tuple(sorted(views, key=lambda item: item.name))


def policy_views(components: RuntimeComponents) -> tuple[PolicyView, ...]:
    views: list[PolicyView] = []
    for domain in components.domain_composition.domains:
        views.extend(policy_view(policy, domain) for policy in domain.policies)
    return tuple(sorted(views, key=lambda item: item.name))


def evaluator_views(components: RuntimeComponents) -> tuple[EvaluatorView, ...]:
    views: list[EvaluatorView] = []
    for domain in components.domain_composition.domains:
        views.extend(evaluator_view(evaluator, domain) for evaluator in domain.evaluators)
    return tuple(sorted(views, key=lambda item: item.name))


def memory_views(components: RuntimeComponents) -> tuple[MemoryView, ...]:
    return tuple(memory_view(record) for record in components.memory_store.export())


def profile_views(registry: ProfileRegistry) -> tuple[ProfileView, ...]:
    return tuple(profile_view(profile) for profile in registry.all())


def multi_agent_view(
    agent_registry: AgentRegistry | None,
    delegation_state: AgentDelegationState,
) -> MultiAgentView:
    if agent_registry is None:
        return MultiAgentView(enabled=False)
    snapshot = agent_registry.snapshot()
    return MultiAgentView(
        enabled=True,
        profiles=tuple(multi_agent_profile_view(item) for item in snapshot.profiles),
        instances=tuple(multi_agent_instance_view(item) for item in snapshot.instances),
        delegation_tasks=tuple(
            MultiAgentDelegationTaskView(
                task_id=str(task.task_id),
                child_count=task.child_count,
                delegation_depth=task.delegation_depth,
            )
            for task in delegation_state.tasks
        ),
    )


def _capability_required_arguments(
    components: RuntimeComponents,
    capability: str,
) -> tuple[str, ...]:
    try:
        return components.resolver.resolve_registration(
            capability
        ).tool.definition.required_arguments
    except (UnknownCapabilityError, CapabilityUnavailableError):
        return ()


def _capability_argument_schema(
    components: RuntimeComponents,
    capability: str,
) -> JsonMapping:
    try:
        return components.resolver.resolve_registration(capability).tool.definition.argument_schema
    except (UnknownCapabilityError, CapabilityUnavailableError):
        return immutable_json()
