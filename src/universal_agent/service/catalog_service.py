from __future__ import annotations

from typing import TYPE_CHECKING

from universal_agent.core import immutable_json
from universal_agent.domain import (
    DomainPackageRegistry,
    DomainPackageVerificationReport,
    RuntimeComponents,
)
from universal_agent.multi_agent import AgentDelegationState, AgentRegistry
from universal_agent.profile import ProfileNotFoundError, ProfileRegistry
from universal_agent.runtime import RuntimeAPI
from universal_agent.security import SecretResolutionReport
from universal_agent.service.catalog import (
    capability_views,
    domain_package_detail,
    domain_package_views,
    domain_views,
    evaluator_views,
    memory_views,
    multi_agent_view,
    policy_views,
    profile_views,
    tool_views,
)
from universal_agent.service.config_views import (
    format_identities,
    not_ready_reason,
    redact_environment,
    runtime_config_domain_views,
    runtime_model_config_view,
    runtime_secret_ref_views,
    secret_readiness_failure,
)
from universal_agent.service.projections import profile_view
from universal_agent.service.views import (
    CapabilityView,
    DomainPackageView,
    DomainView,
    EvaluatorView,
    HealthView,
    MemoryView,
    MultiAgentView,
    PolicyView,
    ProfileView,
    ReadyView,
    RuntimeConfigView,
    ToolView,
)

if TYPE_CHECKING:
    from universal_agent.host.config import RuntimeConfig


class CatalogService:
    """Read-only catalog and configuration metadata over RuntimeComponents.

    Owns health, readiness, domain/tool/capability/policy/evaluator/memory/profile
    listings and the runtime configuration projection. It never executes and never
    mutates runtime state.
    """

    def __init__(
        self,
        *,
        components: RuntimeComponents,
        profiles: ProfileRegistry,
        domain_packages: DomainPackageRegistry,
        config: RuntimeConfig | None,
        secret_resolution: SecretResolutionReport | None,
        runtime_api: RuntimeAPI,
        agent_registry: AgentRegistry | None,
        agent_delegation_state: AgentDelegationState,
    ) -> None:
        self._components = components
        self._profiles = profiles
        self._domain_packages = domain_packages
        self._config = config
        self._secret_resolution = secret_resolution
        self._runtime_api = runtime_api
        self._agent_registry = agent_registry
        self._agent_delegation_state = agent_delegation_state

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
        catalog_ready = bool(domains) and bool(capabilities) and bool(tools) and not missing_tools
        secret_failure = secret_readiness_failure(self._secret_resolution)
        ready = catalog_ready and secret_failure is None
        reason = "ready"
        if not catalog_ready:
            reason = not_ready_reason(
                has_domains=bool(domains),
                has_capabilities=bool(capabilities),
                has_tools=bool(tools),
                missing_tools=missing_tools,
            )
        elif secret_failure is not None:
            reason = secret_failure
        return ReadyView(
            ready=ready,
            reason=reason,
            domain_count=len(domains),
            capability_count=len(capabilities),
            tool_count=len(tools),
        )

    def domains(self) -> tuple[DomainView, ...]:
        return domain_views(self._components)

    def domain_packages(self, *, tag: str | None = None) -> tuple[DomainPackageView, ...]:
        return domain_package_views(self._domain_packages, tag=tag)

    def domain_package(self, name: str, version: str | None = None) -> DomainPackageView:
        return domain_package_detail(self._domain_packages, name, version)

    def domain_package_verification(
        self,
        *,
        verify_paths: bool = False,
    ) -> DomainPackageVerificationReport:
        return self._domain_packages.verify(verify_paths=verify_paths)

    def capabilities(self) -> tuple[CapabilityView, ...]:
        return capability_views(self._components)

    def tools(self) -> tuple[ToolView, ...]:
        return tool_views(self._components)

    def policies(self) -> tuple[PolicyView, ...]:
        return policy_views(self._components)

    def evaluators(self) -> tuple[EvaluatorView, ...]:
        return evaluator_views(self._components)

    def memories(self) -> tuple[MemoryView, ...]:
        return memory_views(self._components)

    def profiles(self) -> tuple[ProfileView, ...]:
        return profile_views(self._profiles)

    def multi_agent(self) -> MultiAgentView:
        return multi_agent_view(self._agent_registry, self._agent_delegation_state)

    def profile(self, name: str) -> ProfileView:
        return profile_view(self._profiles.get(name))

    def accepts_profile(self, name: str) -> bool:
        return self.profile_selection_error(name) is None

    def profile_selection_error(self, name: str) -> str | None:
        try:
            profile = self._profiles.get(name)
        except ProfileNotFoundError:
            return f"unknown profile: {name}"
        profile_domains = tuple(domain.identity() for domain in profile.configured_domains())
        active_domains = self._components.domain_composition.identities
        if profile_domains != active_domains:
            return (
                f"profile {name} is not bound to this RuntimeService: profile domains "
                f"{format_identities(profile_domains)} do not match active runtime domains "
                f"{format_identities(active_domains)}"
            )
        return None

    def config(self) -> RuntimeConfigView:
        identities = self._components.domain_composition.identities
        state_event_commit = self._runtime_api.state_event_commit()
        if self._config is None:
            return RuntimeConfigView(
                environment=immutable_json(),
                domain_package_paths=(),
                secrets=(),
                store_backend="memory",
                store_path=None,
                distributed_queue_backend="memory",
                distributed_queue_path=None,
                distributed_locks_backend="memory",
                distributed_locks_path=None,
                distributed_workers_backend="memory",
                distributed_workers_path=None,
                max_iterations=20,
                max_recovery_steps=8,
                domains=runtime_config_domain_views(identities),
                distributed_terminal_retention_seconds=None,
                state_event_commit_supported=state_event_commit.supported,
                state_event_commit_strategy=state_event_commit.strategy,
                state_event_commit_shared_store=state_event_commit.shared_store,
            )
        return RuntimeConfigView(
            environment=redact_environment(self._config.environment),
            domain_package_paths=self._config.domain_package_paths,
            model=runtime_model_config_view(self._config.model),
            secrets=runtime_secret_ref_views(
                self._config.secrets,
                self._secret_resolution,
            ),
            store_backend=self._config.store.backend.value,
            store_path=self._config.store.path,
            distributed_queue_backend=self._config.distributed_queue.backend.value,
            distributed_queue_path=self._config.distributed_queue.path,
            distributed_locks_backend=self._config.distributed_locks.backend.value,
            distributed_locks_path=self._config.distributed_locks.path,
            distributed_workers_backend=self._config.distributed_workers.backend.value,
            distributed_workers_path=self._config.distributed_workers.path,
            max_iterations=self._config.limits.max_iterations,
            max_recovery_steps=self._config.limits.max_recovery_steps,
            domains=runtime_config_domain_views(
                identities,
                self._config.configured_domains(),
            ),
            distributed_terminal_retention_seconds=(
                self._config.distributed_terminal_retention_seconds
            ),
            state_event_commit_supported=state_event_commit.supported,
            state_event_commit_strategy=state_event_commit.strategy,
            state_event_commit_shared_store=state_event_commit.shared_store,
        )
