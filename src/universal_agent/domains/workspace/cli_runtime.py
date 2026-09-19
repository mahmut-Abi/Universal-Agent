"""Workspace Domain CLI runtime (extension point).

Owns everything workspace-specific about CLI runtime assembly: the service
builder for the sandboxed file-operation domain and the domain config
written by `agent init --domain-backend workspace`.
"""

from __future__ import annotations

from pathlib import Path

from universal_agent import (
    AgentRuntime,
    DomainLoader,
    InMemoryEventSink,
    InMemoryStateStore,
    RuntimeAPI,
    RuntimeBuilder,
    RuntimeService,
    immutable_json,
)
from universal_agent.core import JsonMapping
from universal_agent.domains.workspace import (
    INSPECT_WORKSPACE_CAPABILITY,
    WORKSPACE_DOMAIN_NAME,
    WORKSPACE_DOMAIN_VERSION,
    WorkspaceDomain,
)
from universal_agent.model import ModelAdapter


class WorkspaceDecisionAdapter:
    """Deterministic workspace flow: inspect workspace, then finish."""

    def __init__(self) -> None:
        self._inspected = False

    async def decide(self, context: object) -> object:
        from universal_agent.core import Decision, DecisionType

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
    "build_workspace_service",
    "workspace_domain_config",
    "workspace_domain_config_from_payload",
]
