from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections.abc import Sequence
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, TextIO, cast

from universal_agent.core import Goal, SessionId, Task
from universal_agent_cli.client_config import (
    SERVER_ENV_TOKEN,
    resolve_client_server_target,
    resolve_client_token,
)
from universal_agent_cli.io import (
    CliExit,
    _success_criteria,
    _warn_mutation_goal_without_criteria,
    _write_error,
    _write_json,
    _write_text,
)
from universal_agent_cli.parser import build_parser

if TYPE_CHECKING:
    # Static surface only: resolved lazily at runtime so importing the CLI
    # package stays light (the parser/argparse floor) for commands like
    # `ua --help` that never dispatch.
    from universal_agent.service import RuntimeService
    from universal_agent_cli.serve import ServerRunner

    # Compatibility re-export, resolved at runtime via module __getattr__.
    LOCAL_PROFILE_NAME: str

__all__ = [
    "LOCAL_PROFILE_NAME",
    "build_configured_probe_service",
    "build_configured_service",
    "build_default_service",
    "main",
    "run_cli",
]


def __getattr__(name: str) -> object:
    # Compatibility re-export: resolved via domain contributions so the CLI
    # package never names a concrete domain.
    if name == "LOCAL_PROFILE_NAME":
        from universal_agent_cli.parser import local_profile_name

        return local_profile_name()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _require_tui_module(module_name: str) -> ModuleType:
    """Import an optional universal_agent_tui module lazily ([tui] extra).

    The CLI top level must stay importable without the TUI stack so commands
    like ``ua run`` do not pay for textual; the TUI command surfaces a clear
    install hint when the extra is missing.
    """
    # Allowlist: only the two runtime TUI modules are ever imported here.
    allowed_tui_modules = {"universal_agent_tui.tui", "universal_agent_tui.tui_app"}
    if module_name not in allowed_tui_modules:
        raise ImportError(f"module is not an allowlisted TUI module: {module_name}")
    try:
        return import_module(module_name)
    except ImportError as exc:
        raise ImportError(
            "The interactive TUI requires the optional 'textual' dependency. "
            "Install it with: pip install 'universal-agent-runtime[tui]'"
        ) from exc


def _primary_profile_name(service: RuntimeService) -> str | None:
    profiles = service.profiles()
    return profiles[0].name if profiles else None


def build_default_service() -> RuntimeService:
    """Golden Path default: the domain-neutral Local workspace profile."""

    from universal_agent.host_contracts import build_default_domain_service

    return build_default_domain_service("local")


def build_configured_service(profile_config_path: str | Path) -> RuntimeService:
    """Assemble a RuntimeService from a profile config via the shared SDK dispatch."""

    from universal_agent.facade import build_configured_service as build_shared_service

    return build_shared_service(profile_config_path)


def build_configured_probe_service(profile_config_path: str | Path) -> RuntimeService:
    """Build a probe-style RuntimeService via the domain contribution surface."""

    from universal_agent_cli.contributions import load_cli_contributions

    for contribution in load_cli_contributions():
        if contribution.build_probe_service is not None:
            return contribution.build_probe_service(str(profile_config_path))
    raise ValueError(
        "no domain contributes a probe service; use a profile config with "
        "--profile-config or run `agent init` first"
    )


def _embedded_probe_only(args: argparse.Namespace) -> bool:
    """Whether the command should launch its embedded runtime in probe mode."""

    from universal_agent_cli.parser import domain_contribution_for_command

    contribution = domain_contribution_for_command(cast(str, args.command))
    if contribution is not None and contribution.is_probe_service_command is not None:
        return contribution.is_probe_service_command(args)
    return False


def _embedded_domain_default(args: argparse.Namespace) -> str | None:
    """Domain whose default service backs an embedded runtime launch."""

    from universal_agent_cli.parser import domain_contribution_for_command

    contribution = domain_contribution_for_command(cast(str, args.command))
    if contribution is not None and contribution.uses_embedded_default_service:
        return contribution.domain
    return None


async def run_cli(
    argv: Sequence[str] | None = None,
    *,
    service: RuntimeService | None = None,
    server_runner: ServerRunner | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    prog: str | None = None,
) -> int:
    from universal_agent.distributed import (
        DistributedLockConflictError,
        DistributedLockLeaseLostError,
        WorkerNotFoundError,
        WorkItemNotFoundError,
    )
    from universal_agent.domain import DomainPackageNotFoundError
    from universal_agent.ecosystem import (
        EcosystemRegistryNotFoundError,
        EcosystemRegistryStoreNotFoundError,
    )
    from universal_agent.evaluation.dataset import EvaluationDatasetNotFoundError
    from universal_agent.evaluation.dispatch import DispatchExit
    from universal_agent.memory import MemoryNotFoundError
    from universal_agent.profile import ProfileConfigNotFoundError
    from universal_agent.state import StateNotFoundError
    from universal_agent_api import AgentdClient, AgentdClientError
    from universal_agent_cli.agentd import (
        _agentd_api_token,
        _client_timeout_seconds,
        command_supports_agentd,
        dispatch_agentd_cli,
        dispatch_agentd_commands,
    )
    from universal_agent_cli.remote.client import _profile_headers

    parser = build_parser(prog)
    args = parser.parse_args(list(argv) if argv is not None else None)
    out = stdout or sys.stdout
    err = stderr or sys.stderr

    # Client/server separation: resolve the remote agentd target from the
    # explicit flag, then AGENT_API_URL, then the user config file. When a
    # target is configured, thin-client mode is the default and the embedded
    # runtime is only used as the local fallback.
    client_target = resolve_client_server_target(cast("str | None", args.api_url))
    if client_target is not None:
        args.api_url = client_target.url
        if args.api_token is None and args.api_token_env is None:
            client_token = resolve_client_token(client_target) or os.environ.get(SERVER_ENV_TOKEN)
            if client_token:
                args.api_token = client_token
            elif client_target.auth_token_env:
                args.api_token_env = client_target.auth_token_env

    try:
        is_production_run = (
            cast(str, args.command) == "run"
            and service is None
            and cast(str | None, args.api_url) is None
        )
        if is_production_run:
            # Golden-path production run: no injected service, no agentd.
            # Spawn the embedded runtime and drive its HTTP API.
            from universal_agent_cli.embedded import (
                EmbeddedRuntimeError,
                launch_embedded_runtime,
            )

            try:
                embedded = launch_embedded_runtime(cast(str | None, args.profile_config))
            except EmbeddedRuntimeError as exc:
                _write_error(
                    err,
                    "embedded_runtime_start_failed",
                    f"{exc} — check --profile-config, or run `agent init` / `agent doctor`.",
                )
                return 1
            try:
                async with AgentdClient(
                    embedded.base_url,
                    bearer_token=_agentd_api_token(args),
                    timeout_seconds=_client_timeout_seconds(args),
                    extra_headers=_profile_headers(args),
                ) as embedded_client:
                    await dispatch_agentd_commands(args, out, embedded_client)
            finally:
                embedded.shutdown()
            return 0
        if cast(str | None, args.api_url) is not None:
            if not command_supports_agentd(args):
                raise ValueError(f"command does not support --api-url: {cast(str, args.command)}")
            await dispatch_agentd_cli(args, out)
            return 0
        if _is_config_validate_command(args):
            _dispatch_config_validate(args, out)
            return 0
        if service is not None:
            # Test/embedded-injection path: drive the kernel service directly.
            await _dispatch(args, service, out, server_runner=server_runner)
            return 0
        if cast(str, args.command) == "doctor":
            # Production doctor: local preflight first, then the embedded
            # runtime — the same initialization path `agent run` uses.
            from universal_agent_cli.doctor import run_doctor_command

            return await run_doctor_command(args, out)
        if command_supports_agentd(args) and cast(str, args.command) != "chat":
            # Production path without an injected runtime: serve the runtime
            # in an isolated subprocess and talk to it over its HTTP API.
            # Chat is excluded: locally it runs in-process against the
            # --profile-selected service (the embedded server's startup
            # profile need not match chat's profile).
            from universal_agent_cli.embedded import (
                EmbeddedRuntimeError,
                launch_embedded_runtime,
            )

            try:
                embedded = launch_embedded_runtime(
                    cast("str | None", args.profile_config),
                    probe_only=_embedded_probe_only(args),
                    domain_default=_embedded_domain_default(args),
                )
            except EmbeddedRuntimeError as exc:
                _write_error(
                    err,
                    "embedded_runtime_start_failed",
                    f"{exc} — check --profile-config, or run `agent init` / `agent doctor`.",
                )
                return 1
            try:
                async with AgentdClient(
                    embedded.base_url,
                    bearer_token=_agentd_api_token(args),
                    timeout_seconds=_client_timeout_seconds(args),
                    extra_headers=_profile_headers(args),
                ) as embedded_client:
                    await dispatch_agentd_commands(args, out, embedded_client)
            finally:
                embedded.shutdown()
            return 0
        runtime_service = _service_from_args(args)
        await _dispatch(args, runtime_service, out, server_runner=server_runner)
    except (
        MemoryNotFoundError,
        StateNotFoundError,
        DomainPackageNotFoundError,
        EvaluationDatasetNotFoundError,
        EcosystemRegistryNotFoundError,
        EcosystemRegistryStoreNotFoundError,
        ProfileConfigNotFoundError,
        WorkItemNotFoundError,
        WorkerNotFoundError,
        DistributedLockLeaseLostError,
    ) as exc:
        _write_error(err, "not_found", str(exc))
        return 1
    except AgentdClientError as exc:
        message = exc.code or "agentd_request_failed"
        _write_error(err, message, str(exc))
        return 1 if exc.status_code == 404 else 2
    except CliExit as exc:
        return exc.status
    except DispatchExit as exc:
        return exc.status
    except (ValueError, DistributedLockConflictError) as exc:
        _write_error(err, "bad_request", str(exc))
        return 2
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        script = Path(sys.argv[0]).stem
        prog = script if script in {"agent", "ua"} else None
        return asyncio.run(run_cli(argv, prog=prog))
    except KeyboardInterrupt:
        return 130


def _service_from_args(args: argparse.Namespace) -> RuntimeService:
    from universal_agent_cli.parser import domain_contribution_for_command

    profile_config = cast("str | None", args.profile_config)
    if profile_config is None:
        return build_default_service()
    contribution = domain_contribution_for_command(cast(str, args.command))
    if (
        contribution is not None
        and contribution.build_probe_service is not None
        and contribution.is_probe_service_command is not None
        and contribution.is_probe_service_command(args)
    ):
        return contribution.build_probe_service(profile_config)
    return build_configured_service(profile_config)


def _is_config_validate_command(args: argparse.Namespace) -> bool:
    return cast(str, args.command) == "config" and cast(str, args.config_command) == "validate"


def _dispatch_config_validate(args: argparse.Namespace, out: TextIO) -> None:
    from universal_agent_cli.config import validate_profile_config_file

    profile_config = cast(str | None, args.profile_config)
    if profile_config is None:
        raise ValueError("config validate requires --profile-config")
    report = validate_profile_config_file(
        profile_config,
        check_secrets=not cast(bool, args.skip_secret_resolution),
    )
    _write_json(out, report)
    if report["status"] != "ok":
        raise CliExit(1)


async def _dispatch(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
    *,
    server_runner: ServerRunner | None = None,
) -> None:
    from universal_agent.agentd.representations import (
        capability_body,
        domain_body,
        evaluator_body,
        memory_body,
        policy_body,
        tool_body,
    )
    from universal_agent.memory import MemoryKind
    from universal_agent_cli.catalog_commands import _dispatch_domain_packages, _dispatch_profile
    from universal_agent_cli.distributed import _dispatch_distributed
    from universal_agent_cli.ecosystem import _dispatch_ecosystem
    from universal_agent_cli.evaluation import _dispatch_eval
    from universal_agent_cli.init import _dispatch_init
    from universal_agent_cli.observability import _dispatch_observability
    from universal_agent_cli.serve import _dispatch_serve
    from universal_agent_cli.session import _dispatch_session

    command = cast(str, args.command)
    if command == "version":
        _write_json(out, {"version": _package_version()})
        return
    if command in {
        "health",
        "ready",
        "metrics",
        "cost",
        "logs",
        "traces",
        "doctor",
        "audit",
        "multi-agent",
        "repair",
    }:
        await _dispatch_observability(args, service, out)
        return
    if command == "distributed":
        await _dispatch_distributed(args, service, out)
        return
    if command == "init":
        _dispatch_init(args, out)
        return
    if command == "config":
        _dispatch_config(args, service, out)
        return
    if command == "serve":
        await _dispatch_serve(args, service, out, server_runner=server_runner)
        return
    if command == "run":
        await _dispatch_run(args, service, out)
        return
    from universal_agent_cli.parser import domain_contribution_for_command

    contribution = domain_contribution_for_command(command)
    if contribution is not None and contribution.dispatch is not None:
        outcome = await contribution.dispatch(args, service)
        _write_json(out, outcome.payload)
        if outcome.status != 0:
            raise CliExit(outcome.status)
        return
    if command == "tui":
        await _dispatch_tui(args, service, out)
        return
    if command == "ecosystem":
        _dispatch_ecosystem(args, out)
        return
    if command == "eval":
        await _dispatch_eval(args, service, out)
        return
    if command == "domain":
        _write_json(out, {"domains": [domain_body(item) for item in service.domains()]})
        return
    if command == "domain-packages":
        _dispatch_domain_packages(args, service, out)
        return
    if command == "profile":
        _dispatch_profile(args, service, out)
        return
    if command == "capabilities":
        _write_json(
            out,
            {"capabilities": [capability_body(item) for item in service.capabilities()]},
        )
        return
    if command == "tools":
        _write_json(out, {"tools": [tool_body(item) for item in service.tools()]})
        return
    if command == "policies":
        _write_json(out, {"policies": [policy_body(item) for item in service.policies()]})
        return
    if command == "evaluators":
        _write_json(
            out,
            {"evaluators": [evaluator_body(item) for item in service.evaluators()]},
        )
        return
    if command == "chat":
        await _dispatch_chat(args, service, out)
        return
    if command == "memory":
        memory_command = cast(str | None, getattr(args, "memory_command", None))
        if memory_command == "add":
            view = service.create_memory(
                kind=MemoryKind(cast(str, args.kind)),
                subject=cast(str, args.subject),
                content=cast(str, args.content),
                scope=cast(str, args.scope),
                confidence=cast(float, args.confidence),
            )
            _write_json(out, memory_body(view))
            return
        if memory_command == "get":
            _write_json(out, memory_body(service.require_memory(cast(str, args.memory_id))))
            return
        if memory_command == "delete":
            service.require_memory(cast(str, args.memory_id))
            service.delete_memory(cast(str, args.memory_id))
            _write_json(out, {"deleted": True, "memory_id": cast(str, args.memory_id)})
            return
        _write_json(out, {"memories": [memory_body(item) for item in service.memories()]})
        return
    if command == "session":
        await _dispatch_session(args, service, out)
        return
    raise ValueError(f"unknown command: {command}")


async def _dispatch_run(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    from universal_agent.agentd.representations import event_batch_body, runtime_run_body
    from universal_agent_cli.text_views import render_run_text

    profile = _resolve_run_profile(args, service)
    criteria = _success_criteria(cast(list[str], args.success))
    _warn_mutation_goal_without_criteria(cast(str, args.goal), cast(list[str], args.success))
    goal = Goal(cast(str, args.goal), criteria)
    timeout_seconds = _run_timeout_seconds(args)
    started = time.monotonic()
    read_only = cast(bool, getattr(args, "dry_run", False))
    if cast(bool, args.compile_goal):
        if cast(str | None, args.task) is not None:
            raise ValueError("--task cannot be used with --compile-goal")
        run = await service.run_compiled_goal(goal, timeout_seconds=timeout_seconds)
    else:
        task = Task(cast(str | None, args.task) or "Run goal", tuple(item.key for item in criteria))
        run = await service.run_goal(
            goal,
            task,
            timeout_seconds=timeout_seconds,
            read_only=read_only,
        )
    duration_seconds = time.monotonic() - started
    body = runtime_run_body(run)
    if cast(str, args.output) == "json":
        _write_json(out, body)
        return
    events_body = event_batch_body(await service.stream_events(run.result.session_id, limit=500))
    _write_text(
        out,
        render_run_text(
            body,
            duration_seconds=duration_seconds,
            events_body=events_body,
            profile=profile,
        ),
    )


def _run_timeout_seconds(args: argparse.Namespace) -> float | None:
    value = cast(float | None, getattr(args, "timeout_seconds", None))
    if value is None:
        return None
    if value <= 0:
        raise ValueError("--timeout-seconds must be greater than 0")
    return value


def _resolve_run_profile(args: argparse.Namespace, service: RuntimeService) -> str | None:
    """Resolve the run profile: --profile flag > positional > service primary."""

    flag = cast(str | None, getattr(args, "profile_option", None))
    positional = cast(str | None, getattr(args, "profile", None))
    if flag is not None and positional is not None and flag != positional:
        raise ValueError(
            "run accepts one profile: use --profile or the positional argument, not both"
        )
    selected = flag if flag is not None else positional
    if selected is not None:
        error = service.profile_selection_error(selected)
        if error is not None:
            raise ValueError(f"unknown profile: {selected}")
        return selected
    profiles = service.profiles()
    return profiles[0].name if profiles else None


async def _dispatch_chat(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    """Interactive conversation: each line becomes a goal run on the runtime."""

    profile = cast("str | None", args.profile)
    if profile is None:
        # Follow the service's primary profile so chat works against any
        # runtime (embedded, remote, or user-config mismatch).
        primary = _primary_profile_name(service)
        if primary is None:
            raise ValueError("service exposes no profiles; pass --profile explicitly")
        profile = primary
        args.profile = profile
    if not service.accepts_profile(profile):
        raise ValueError(f"unknown profile: {profile}")
    show_events = cast(bool, args.show_events)
    _write_text(
        out,
        f"Universal Agent chat — profile {profile}. "
        "Type a goal per line; /exit quits, /help shows help.\n",
    )
    while True:
        try:
            line = (await asyncio.to_thread(input, "you> ")).strip()
        except EOFError:
            break
        if not line:
            continue
        if line in {"/exit", "/quit", "exit", "quit"}:
            break
        if line == "/help":
            _write_text(out, "Type a goal per line. /exit quits. /help shows this.\n")
            continue
        goal = Goal(line, ())
        run = await service.run_goal(goal, Task("Chat turn", ()))
        result = run.result
        _write_text(out, f"[{result.status.value}] {result.reason or ''}\n")
        if show_events:
            batch = await service.stream_events(result.session_id, limit=8)
            for event in batch.events:
                _write_text(out, f"  · {event.type} {event.occurred_at:%H:%M:%S}\n")
    _write_text(out, "bye\n")


async def _dispatch_tui(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    tui_module = _require_tui_module("universal_agent_tui.tui")
    tui_app_module = _require_tui_module("universal_agent_tui.tui_app")
    build_tui_snapshot = tui_module.build_tui_snapshot
    render_tui_snapshot = tui_module.render_tui_snapshot
    RuntimeTuiApp = tui_app_module.RuntimeTuiApp
    service_tui_actions = tui_app_module.service_tui_actions
    session_id = cast(str | None, args.session_id)
    if cast(bool, args.static):
        snapshot = await build_tui_snapshot(
            service,
            session_id=None if session_id is None else SessionId(session_id),
            session_limit=cast(int, args.session_limit),
            event_limit=cast(int, args.event_limit),
        )
        _write_text(out, render_tui_snapshot(snapshot))
        return
    app = RuntimeTuiApp(
        service,
        session_id=None if session_id is None else SessionId(session_id),
        session_limit=cast(int, args.session_limit),
        event_limit=cast(int, args.event_limit),
        actions=service_tui_actions(service),
    )
    await app.run_async()


def _dispatch_config(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    from universal_agent.agentd.representations import config_body, policy_body
    from universal_agent_cli.text_views import render_config_text

    command = cast(str | None, args.config_command)
    profile_config_path = _config_scope_path(args)
    if command is None:
        # Golden path: bare `agent config` shows the effective configuration as
        # human-readable text (never secrets).
        _write_text(
            out,
            render_config_text(
                config_body(service.config()),
                profile_config_path=profile_config_path,
                config_dir=_config_settings_dir(args),
                active_profile=_primary_profile_name(service),
                policies_body={"policies": [policy_body(item) for item in service.policies()]},
            ),
        )
        return
    if command == "show":
        body = config_body(service.config())
        if cast(str, getattr(args, "output", "json")) == "text":
            _write_text(
                out,
                render_config_text(
                    body,
                    profile_config_path=profile_config_path,
                    config_dir=_config_settings_dir(args),
                    active_profile=_primary_profile_name(service),
                    policies_body={"policies": [policy_body(item) for item in service.policies()]},
                ),
            )
            return
        _write_json(out, body)
        return
    raise ValueError(f"unknown config command: {command}")


def _config_scope_path(args: argparse.Namespace) -> str | None:
    """Where the profile config came from: explicit flag, discovery, or None."""

    explicit = cast(str | None, args.profile_config)
    if explicit is not None:
        return explicit
    from universal_agent.profile import default_profile_config_path

    discovered = default_profile_config_path()
    return str(discovered) if discovered.is_file() else None


def _config_settings_dir(args: argparse.Namespace) -> str | None:
    """The `agent init` settings directory, when it exists."""

    scope = _config_scope_path(args)
    if scope is None:
        return None
    config_dir = Path(scope).expanduser().parent
    settings = config_dir / "config.json"
    return str(config_dir) if settings.is_file() else None


def _package_version() -> str:
    try:
        return version("universal-agent-runtime")
    except PackageNotFoundError:
        return "0.1.0"


if __name__ == "__main__":
    raise SystemExit(main())
