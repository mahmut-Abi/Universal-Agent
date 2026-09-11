"""Golden Path local runtime assembly.

Builds the domain-neutral `default` Profile service used by `agent run`,
`agent init` defaults and doctor: the read-only Local domain, the deterministic
scripted workspace decision adapter, and the profile written by `agent init`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from universal_agent.core import (
    Decision,
    DecisionContext,
    DecisionType,
    immutable_json,
)
from universal_agent.distributed import DistributedRuntimeCoordinator
from universal_agent.domain import DomainLoader, RuntimeBuilder
from universal_agent.domains.local import (
    LOCAL_DOMAIN_NAME,
    LOCAL_DOMAIN_VERSION,
    WORKSPACE_CAPABILITY,
    LocalDomain,
)
from universal_agent.host import (
    DomainConfig,
    RuntimeConfig,
    RuntimeHost,
    RuntimeLimitsConfig,
    StoreConfig,
)
from universal_agent.model import ModelAdapter
from universal_agent.profile import AgentProfile, ProfileConfig
from universal_agent.runtime import AgentRuntime, InMemoryEventSink, RuntimeAPI
from universal_agent.security import EnvSecretProvider
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore

LocalModelAdapterBuilder = Callable[..., ModelAdapter]

LOCAL_PROFILE_NAME = "default"
LOCAL_STORE_CONFIG = RuntimeConfig(
    environment=immutable_json({"environment": "local"}),
    store=StoreConfig.memory(),
    limits=RuntimeLimitsConfig(max_iterations=12, max_recovery_steps=4),
    domain=DomainConfig(LOCAL_DOMAIN_NAME, LOCAL_DOMAIN_VERSION),
)


class WorkspaceDecisionAdapter:
    """Deterministic domain-neutral decision flow: inspect workspace, finish."""

    def __init__(self) -> None:
        self.contexts: list[DecisionContext] = []
        self._inspected = False

    async def decide(self, context: DecisionContext) -> Decision:
        self.contexts.append(context)
        if not self._inspected:
            self._inspected = True
            return Decision(
                DecisionType.EXECUTE,
                "Summarize the local workspace",
                capability=WORKSPACE_CAPABILITY,
                target="workspace",
                arguments=immutable_json({}),
                expected_observations=("healthy",),
            )
        expected = {
            criterion.key: criterion.expected for criterion in context.goal_success_criteria
        }
        required = set(context.current_task_required_criteria)
        matched = {
            key: value
            for key, value in context.satisfied_criteria.items()
            if key in expected or key in required
        }
        if (
            (expected or required)
            and set(expected).issubset(matched)
            and required.issubset(matched)
        ):
            return Decision(DecisionType.FINISH, "Workspace inspection satisfied the goal")
        # Unknown criteria (e.g. --success resource=...) cannot be produced by
        # the read-only local domain; finish honestly instead of looping.
        return Decision(
            DecisionType.FINISH,
            "Local workspace inspection complete; further criteria are out of scope",
        )


def local_profile(
    *,
    runtime_config: RuntimeConfig | None = None,
    name: str = LOCAL_PROFILE_NAME,
) -> AgentProfile:
    config = runtime_config or LOCAL_STORE_CONFIG
    domain = DomainConfig(LOCAL_DOMAIN_NAME, LOCAL_DOMAIN_VERSION)
    return AgentProfile(
        name,
        "0.1.0",
        "Generic local Agent profile (read-only workspace domain).",
        domain,
        config,
        (domain,),
    )


def build_local_service(
    *,
    profile: AgentProfile | None = None,
    model: ModelAdapter | None = None,
    config: RuntimeConfig | None = None,
) -> RuntimeService:
    """Assemble the read-only Local-domain RuntimeService (no Kubernetes)."""

    resolved_profile = profile or local_profile(runtime_config=config)
    components = RuntimeBuilder().build(DomainLoader().load(LocalDomain()))
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=model or WorkspaceDecisionAdapter(),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "local"}),
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
        profiles=(resolved_profile,),
        config=resolved_profile.runtime,
        distributed_coordinator=DistributedRuntimeCoordinator(),
    )


def build_local_profile_service(profile_config_path: str | Path) -> RuntimeService:
    """Build a RuntimeService from an `agent init` profile whose domain is local."""

    profile_config = ProfileConfig.from_json_file(profile_config_path)
    profile = profile_config.to_profile()
    secret_provider = EnvSecretProvider()
    from universal_agent.host import build_configured_model_adapter

    host = RuntimeHost.from_profile(
        profile=profile,
        model=(
            WorkspaceDecisionAdapter()
            if profile.runtime.model.provider.value == "scripted"
            else build_configured_model_adapter(
                profile.runtime,
                secret_provider=secret_provider,
            )
        ),
        domain=LocalDomain(),
        secret_provider=secret_provider,
    )
    return host.service


def local_domain_config() -> dict[str, object]:
    return {"name": LOCAL_DOMAIN_NAME, "version": LOCAL_DOMAIN_VERSION}


__all__ = [
    "LOCAL_PROFILE_NAME",
    "WorkspaceDecisionAdapter",
    "build_local_profile_service",
    "build_local_service",
    "local_domain_config",
    "local_profile",
]
