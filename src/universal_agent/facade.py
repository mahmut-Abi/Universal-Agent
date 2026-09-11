"""The public Agent facade (P0 Golden Path embedding API).

A normal user should not need to understand RuntimeHost / RuntimeService /
DomainManager internals to run an Agent:

    from universal_agent import Agent

    agent = Agent.from_profile("default")
    result = await agent.run("Analyze this project")

`from_profile` loads the profile config created by `agent init` (project-local
`universal-agent/profile.json`, then the user-level config), assembles the
Runtime through the standard RuntimeHost boundary, and exposes a small async
API for running goals and reading sessions. The facade never bypasses the
Kernel: goals still flow through decision validation, policy, tool execution,
observation, evidence and evaluation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from os import environ as os_environ
from pathlib import Path

from universal_agent.core import Goal, JsonValue, SessionId, SuccessCriterion, Task
from universal_agent.core.config_validation import parse_json_value, parse_non_empty_string
from universal_agent.host import RuntimeHost, build_configured_model_adapter
from universal_agent.profile import ProfileConfig, default_profile_config_path
from universal_agent.runtime import RuntimeEventBatch, SessionSummaryView, SessionView
from universal_agent.security import EnvSecretProvider
from universal_agent.service import RuntimeService
from universal_agent.service.sdk import SDKRunResult

__all__ = ["Agent", "AgentConfigurationError"]


class AgentConfigurationError(RuntimeError):
    """Raised when no usable Agent profile configuration can be found."""


class Agent:
    """User-facing Agent handle over one configured RuntimeService."""

    def __init__(self, service: RuntimeService, *, profile: str | None = None) -> None:
        self._service = service
        self._profile = profile

    @classmethod
    def from_profile(
        cls,
        name: str = "default",
        *,
        config_path: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
        store_path: str | Path | None = None,
    ) -> Agent:
        """Assemble an Agent from the profile config written by `agent init`.

        Config discovery order: explicit ``config_path``, then the standard
        profile config location (``universal-agent/profile.json`` in the
        project, or the user-level config directory). ``store_path`` overrides
        the configured session store directory (defaults to the profile's
        file-backed store when `agent init` wrote one).
        """

        path = (
            Path(config_path)
            if config_path is not None
            else default_profile_config_path(environ or dict(os_environ))
        )
        if not path.is_file():
            raise AgentConfigurationError(
                f"no Agent profile config found at {path}; run `agent init` first "
                "(or pass config_path=<path>)"
            )
        profile_config = ProfileConfig.from_json_file(path)
        if profile_config.name != name:
            raise AgentConfigurationError(
                f"profile config {path} defines profile '{profile_config.name}', not '{name}'; "
                "use Agent.from_profile('<defined name>') or re-run `agent init`"
            )
        return cls(_build_service(path, store_path=store_path), profile=name)

    @property
    def service(self) -> RuntimeService:
        """The assembled RuntimeService (for advanced/observability use)."""

        return self._service

    @property
    def profile(self) -> str | None:
        return self._profile

    async def run(
        self,
        goal: str,
        *,
        success_criteria: Mapping[str, JsonValue] | None = None,
        task: str | None = None,
        task_required_criteria: Sequence[str] | None = None,
    ) -> SDKRunResult:
        """Run one goal to a terminal state and return the run result.

        ``task_required_criteria`` overrides the initial task's required
        criteria (default: the goal's success criterion keys, matching the
        CLI ``run`` behavior).
        """

        parsed = tuple(
            SuccessCriterion(key, parse_json_value(value, f"success_criteria.{key}"))
            for key, value in (success_criteria or {}).items()
        )
        required = (
            tuple(task_required_criteria)
            if task_required_criteria is not None
            else tuple(item.key for item in parsed)
        )
        goal_obj = Goal(_description(goal), parsed)
        task_obj = Task(task or "Run goal", required)
        return SDKRunResult.from_runtime(await self._service.run_goal(goal_obj, task_obj))

    async def resume(self, session_id: str, *, confirmed: bool | None = None) -> SDKRunResult:
        """Resume a waiting session (e.g. a policy confirmation) on the runtime."""

        return SDKRunResult.from_runtime(
            await self._service.resume_session(SessionId(session_id), confirmed=confirmed)
        )

    async def cancel(
        self, session_id: str, *, reason: str = "cancelled by Agent facade"
    ) -> SDKRunResult:
        return SDKRunResult.from_runtime(
            await self._service.cancel_session(SessionId(session_id), reason=reason)
        )

    async def pause(
        self, session_id: str, *, reason: str = "paused by Agent facade"
    ) -> SDKRunResult:
        return SDKRunResult.from_runtime(
            await self._service.pause_session(SessionId(session_id), reason=reason)
        )

    async def sessions(self) -> tuple[SessionSummaryView, ...]:
        batch = await self._service.stream_sessions()
        return batch.sessions

    async def session(self, session_id: str) -> SessionView:
        return await self._service.get_session(SessionId(session_id))

    async def events(self, session_id: str, *, limit: int | None = None) -> RuntimeEventBatch:
        return await self._service.stream_events(SessionId(session_id), limit=limit)


def _description(goal: str) -> str:
    return parse_non_empty_string(goal, "goal description")


def _build_service(
    config_path: str | Path,
    *,
    store_path: str | Path | None = None,
) -> RuntimeService:
    profile_config = (
        ProfileConfig.from_json_file(config_path)
        if store_path is None
        else _with_store_path(ProfileConfig.from_json_file(config_path), Path(store_path))
    )
    profile = profile_config.to_profile()
    secret_provider = EnvSecretProvider()
    if profile.runtime.domain_package_paths:
        return RuntimeHost.from_configured_domain_packages(
            config=profile.runtime,
            model=build_configured_model_adapter(profile.runtime, secret_provider=secret_provider),
            profile=profile,
            secret_provider=secret_provider,
        ).service
    from universal_agent.domains.kubernetes.cli_runtime import build_configured_service

    return build_configured_service(config_path)


def _with_store_path(config: ProfileConfig, store_path: Path) -> ProfileConfig:
    from dataclasses import replace

    from universal_agent.host import RuntimeConfig, StoreConfig

    runtime = replace(
        config.runtime,
        store=StoreConfig.file(str(store_path))
        if config.runtime.store.backend.value != "sqlite"
        else StoreConfig.sqlite(str(store_path)),
    )
    assert isinstance(runtime, RuntimeConfig)
    return replace(config, runtime=runtime)
