from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TextIO, cast

from universal_agent.agentd.representations import (
    capability_body,
    config_body,
    domain_body,
    evaluator_body,
    memory_body,
    policy_body,
    runtime_run_body,
    tool_body,
)
from universal_agent.core import Goal, SessionId, Task
from universal_agent.distributed import (
    DistributedLockConflictError,
    DistributedLockLeaseLostError,
    WorkerNotFoundError,
    WorkItemNotFoundError,
)
from universal_agent.domain import DomainPackageNotFoundError
from universal_agent.domains.kubernetes.cli import (
    LOCAL_PROFILE_NAME,
    dispatch_kubernetes,
    is_kubernetes_probe_service_command,
)
from universal_agent.domains.kubernetes.cli import (
    build_configured_probe_service as build_kubernetes_configured_probe_service,
)
from universal_agent.domains.kubernetes.cli import (
    build_configured_service as build_kubernetes_configured_service,
)
from universal_agent.domains.kubernetes.cli import (
    build_default_service as build_kubernetes_default_service,
)
from universal_agent.ecosystem import (
    EcosystemRegistryNotFoundError,
    EcosystemRegistryStoreNotFoundError,
)
from universal_agent.evaluation.dataset import EvaluationDatasetNotFoundError
from universal_agent.host.runtime import RuntimeHost, build_configured_model_adapter
from universal_agent.memory import MemoryKind, MemoryNotFoundError
from universal_agent.profile import ProfileConfig, ProfileConfigNotFoundError
from universal_agent.security import EnvSecretProvider
from universal_agent.service import RuntimeService
from universal_agent.state import StateNotFoundError
from universal_agent_api import AgentdClientError
from universal_agent_cli.agentd import command_supports_agentd, dispatch_agentd_cli
from universal_agent_cli.catalog_commands import _dispatch_domain_packages, _dispatch_profile
from universal_agent_cli.config import validate_profile_config_file
from universal_agent_cli.distributed import _dispatch_distributed
from universal_agent_cli.ecosystem import _dispatch_ecosystem
from universal_agent_cli.evaluation import _dispatch_eval
from universal_agent_cli.init import _dispatch_init
from universal_agent_cli.io import (
    CliExit,
    _success_criteria,
    _write_error,
    _write_json,
    _write_text,
)
from universal_agent_cli.observability import _dispatch_observability
from universal_agent_cli.parser import build_parser
from universal_agent_cli.serve import ServerRunner, _dispatch_serve
from universal_agent_cli.session import _dispatch_session
from universal_agent_tui.tui import build_tui_snapshot, render_tui_snapshot
from universal_agent_tui.tui_app import RuntimeTuiApp, service_tui_actions


def build_default_service() -> RuntimeService:
    return build_kubernetes_default_service()


def build_configured_service(profile_config_path: str | Path) -> RuntimeService:
    profile = ProfileConfig.from_json_file(profile_config_path).to_profile()
    if profile.runtime.domain_package_paths:
        secret_provider = EnvSecretProvider()
        return RuntimeHost.from_configured_domain_packages(
            config=profile.runtime,
            model=build_configured_model_adapter(
                profile.runtime,
                secret_provider=secret_provider,
            ),
            profile=profile,
            secret_provider=secret_provider,
        ).service
    return build_kubernetes_configured_service(
        profile_config_path,
        model_adapter_builder=build_configured_model_adapter,
    )


def build_configured_probe_service(profile_config_path: str | Path) -> RuntimeService:
    """Build RuntimeService metadata without requiring the configured model to connect."""

    return build_kubernetes_configured_probe_service(profile_config_path)


async def run_cli(
    argv: Sequence[str] | None = None,
    *,
    service: RuntimeService | None = None,
    server_runner: ServerRunner | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    out = stdout or sys.stdout
    err = stderr or sys.stderr

    try:
        if cast(str | None, args.api_url) is not None:
            if not command_supports_agentd(args):
                raise ValueError(f"command does not support --api-url: {cast(str, args.command)}")
            await dispatch_agentd_cli(args, out)
            return 0
        if _is_config_validate_command(args):
            _dispatch_config_validate(args, out)
            return 0
        runtime_service = service or _service_from_args(args)
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
        message = exc.code if exc.code else "agentd_request_failed"
        _write_error(err, message, str(exc))
        return 1 if exc.status_code == 404 else 2
    except CliExit as exc:
        return exc.status
    except (ValueError, DistributedLockConflictError) as exc:
        _write_error(err, "bad_request", str(exc))
        return 2
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(run_cli(argv))
    except KeyboardInterrupt:
        return 130


def _service_from_args(args: argparse.Namespace) -> RuntimeService:
    profile_config = cast(str | None, args.profile_config)
    if profile_config is None:
        return build_default_service()
    if is_kubernetes_probe_service_command(args):
        return build_configured_probe_service(profile_config)
    return build_configured_service(profile_config)


def _is_config_validate_command(args: argparse.Namespace) -> bool:
    return cast(str, args.command) == "config" and cast(str, args.config_command) == "validate"


def _dispatch_config_validate(args: argparse.Namespace, out: TextIO) -> None:
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
    if command == "kubernetes":
        result = await dispatch_kubernetes(
            args,
            service,
            model_adapter_builder=build_configured_model_adapter,
        )
        _write_json(out, result.payload)
        if result.status != 0:
            raise CliExit(result.status)
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
    profile = cast(str, args.profile)
    if not service.accepts_profile(profile):
        raise ValueError(f"unknown profile: {profile}")
    criteria = _success_criteria(cast(list[str], args.success))
    goal = Goal(cast(str, args.goal), criteria)
    if cast(bool, args.compile_goal):
        if cast(str | None, args.task) is not None:
            raise ValueError("--task cannot be used with --compile-goal")
        run = await service.run_compiled_goal(goal)
    else:
        task = Task(cast(str | None, args.task) or "Run goal", tuple(item.key for item in criteria))
        run = await service.run_goal(goal, task)
    _write_json(out, runtime_run_body(run))


async def _dispatch_chat(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    """Interactive conversation: each line becomes a goal run on the runtime."""

    profile = cast(str, args.profile)
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
    command = cast(str, args.config_command)
    if command == "show":
        _write_json(out, config_body(service.config()))
        return
    raise ValueError(f"unknown config command: {command}")


def _package_version() -> str:
    try:
        return version("universal-agent-runtime")
    except PackageNotFoundError:
        return "0.1.0"


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "LOCAL_PROFILE_NAME",
    "build_configured_probe_service",
    "build_configured_service",
    "build_default_service",
    "main",
    "run_cli",
]
