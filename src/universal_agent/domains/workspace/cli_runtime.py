"""Workspace Domain CLI runtime (extension point).

Owns everything workspace-specific about CLI runtime assembly: the service
builder for the sandboxed file-operation domain and the domain config
written by `agent init --domain-backend workspace`.
"""

from __future__ import annotations

from pathlib import Path

from universal_agent.core import (
    Decision,
    DecisionContext,
    DecisionType,
    JsonMapping,
    immutable_json,
)
from universal_agent.domain import DomainLoader, RuntimeBuilder
from universal_agent.domains.workspace.domain import WorkspaceDomain
from universal_agent.domains.workspace.names import (
    INSPECT_WORKSPACE_CAPABILITY,
    WORKSPACE_DOMAIN_NAME,
    WORKSPACE_DOMAIN_VERSION,
)
from universal_agent.eventstream import InMemoryEventSink
from universal_agent.model import ModelAdapter

# Concrete imports: the lazy `universal_agent.__getattr__` surface resolves to
# Any under pyright, which breaks callability checks in this module.
from universal_agent.runtime import AgentRuntime
from universal_agent.runtime.api import RuntimeAPI
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore


class WorkspaceDecisionAdapter:
    """Deterministic workspace flow: inspect workspace, then finish."""

    def __init__(self) -> None:
        self._inspected = False

    async def decide(self, context: DecisionContext) -> Decision:
        if not self._inspected:
            self._inspected = True
            return Decision(
                DecisionType.EXECUTE,
                "Summarize the workspace",
                capability=INSPECT_WORKSPACE_CAPABILITY,
                target="workspace",
                arguments=immutable_json({}),
                expected_observations=("healthy",),
            )
        return Decision(DecisionType.FINISH, "workspace inspection complete")

    def model_usage(self) -> None:
        return None


def build_workspace_service(
    workspace: Path | None = None,
    *,
    model: ModelAdapter | None = None,
) -> RuntimeService:
    """Assemble the WorkspaceDomain RuntimeService on a sandboxed directory."""

    components = RuntimeBuilder().build(DomainLoader().load(WorkspaceDomain(workspace)))
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=model or WorkspaceDecisionAdapter(),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "workspace"}),
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )


def build_workspace_profile_service(profile_config_path: str | Path) -> RuntimeService:
    """Build a RuntimeService from an `agent init` profile whose domain is
    workspace. Honors the domain settings' ``workspace_path``."""

    from universal_agent.host import RuntimeHost, build_configured_model_adapter
    from universal_agent.profile import ProfileConfig
    from universal_agent.security import EnvSecretProvider

    profile_config = ProfileConfig.from_json_file(profile_config_path)
    profile = profile_config.to_profile()
    secret_provider = EnvSecretProvider()
    settings = profile_config.domain.settings if profile_config.domain else {}
    workspace_path = (
        str(settings.get("workspace_path", ".") or ".") if isinstance(settings, dict) else "."
    )
    model = (
        WorkspaceDecisionAdapter()
        if profile.runtime.model.provider.value == "scripted"
        else build_configured_model_adapter(
            profile.runtime,
            secret_provider=secret_provider,
        )
    )
    host = RuntimeHost.from_profile(
        profile=profile,
        model=model,
        domain=WorkspaceDomain(Path(workspace_path)),
        secret_provider=secret_provider,
    )
    return host.service


def build_default_workspace_service() -> RuntimeService:
    """Default-domain factory for the embedded agentd / host resolution.

    Signature matches the ``universal_agent.default_domains`` entry-point
    group (no arguments); the sandbox defaults to the process working
    directory, matching how the kubernetes default service uses defaults
    rather than reading profile settings.
    """

    return build_workspace_service()


def workspace_domain_config(workspace_path: str = ".") -> dict[str, object]:
    """Domain config payload written by `agent init --domain-backend workspace`."""

    return {
        "name": WORKSPACE_DOMAIN_NAME,
        "version": WORKSPACE_DOMAIN_VERSION,
        "settings": {"workspace_path": workspace_path},
    }


def workspace_domain_config_from_payload(payload: JsonMapping) -> dict[str, object]:
    """Rebuild the domain config from a stored profile payload."""

    settings = payload.get("settings")
    workspace_path = "."
    if isinstance(settings, dict):
        workspace_path = str(settings.get("workspace_path", "."))
    return workspace_domain_config(workspace_path)


__all__ = [
    "WORKSPACE_DOMAIN_NAME",
    "WORKSPACE_DOMAIN_VERSION",
    "WorkspaceDecisionAdapter",
    "build_default_workspace_service",
    "build_workspace_profile_service",
    "build_workspace_service",
    "workspace_domain_config",
    "workspace_domain_config_from_payload",
]
