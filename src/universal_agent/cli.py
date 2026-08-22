from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from importlib.metadata import PackageNotFoundError, version
from typing import TextIO, cast

from universal_agent.agentd.app import (
    capability_body,
    domain_body,
    event_batch_body,
    health_body,
    profile_body,
    ready_body,
    runtime_run_body,
    session_body,
    session_summary_body,
    tool_body,
)
from universal_agent.core import EventId, JsonMapping, SessionId, immutable_json
from universal_agent.domain import DomainLoader, RuntimeBuilder
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.model import ScriptedModelAdapter
from universal_agent.runtime import AgentRuntime, InMemoryEventSink, RuntimeAPI
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore, StateNotFoundError


class _DefaultCliBackend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        resource = arguments.get("name", "example")
        return immutable_json(
            {
                "resource": f"deployment/{resource}",
                "healthy": True,
                "capability": capability,
            }
        )

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        resource = arguments.get("name", "example")
        return immutable_json(
            {
                "resource": f"deployment/{resource}",
                "mutation_applied": True,
                "capability": capability,
            }
        )


def build_default_service() -> RuntimeService:
    backend = _DefaultCliBackend()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(()),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "local"}),
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )


async def run_cli(
    argv: Sequence[str] | None = None,
    *,
    service: RuntimeService | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    runtime_service = service or build_default_service()

    try:
        await _dispatch(args, runtime_service, out)
    except StateNotFoundError as exc:
        err.write(f"{exc}\n")
        return 1
    except ValueError as exc:
        err.write(f"{exc}\n")
        return 2
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run_cli(argv))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("version")
    commands.add_parser("health")
    commands.add_parser("ready")

    domain = commands.add_parser("domain")
    domain_commands = domain.add_subparsers(dest="domain_command", required=True)
    domain_commands.add_parser("list")

    profile = commands.add_parser("profile")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_commands.add_parser("list")

    capabilities = commands.add_parser("capabilities")
    capabilities_commands = capabilities.add_subparsers(
        dest="capabilities_command",
        required=True,
    )
    capabilities_commands.add_parser("list")

    tools = commands.add_parser("tools")
    tools_commands = tools.add_subparsers(dest="tools_command", required=True)
    tools_commands.add_parser("list")

    session = commands.add_parser("session")
    session_commands = session.add_subparsers(dest="session_command", required=True)

    session_commands.add_parser("list")

    show = session_commands.add_parser("show")
    show.add_argument("session_id")

    events = session_commands.add_parser("events")
    events.add_argument("session_id")
    events.add_argument("--after")
    events.add_argument("--limit", type=int)

    pause = session_commands.add_parser("pause")
    pause.add_argument("session_id")
    pause.add_argument("--reason", default="session paused from CLI")

    resume = session_commands.add_parser("resume")
    resume.add_argument("session_id")
    resume.add_argument("--confirmed", choices=("true", "false"))

    cancel = session_commands.add_parser("cancel")
    cancel.add_argument("session_id")
    cancel.add_argument("--reason", default="session cancelled from CLI")

    return parser


async def _dispatch(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    command = cast(str, args.command)
    if command == "version":
        _write_json(out, {"version": _package_version()})
        return
    if command == "health":
        _write_json(out, health_body(service.health()))
        return
    if command == "ready":
        _write_json(out, ready_body(service.ready()))
        return
    if command == "domain":
        _write_json(out, {"domains": [domain_body(item) for item in service.domains()]})
        return
    if command == "profile":
        _write_json(out, {"profiles": [profile_body(item) for item in service.profiles()]})
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
    if command == "session":
        await _dispatch_session(args, service, out)
        return
    raise ValueError(f"unknown command: {command}")


async def _dispatch_session(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    command = cast(str, args.session_command)
    if command == "list":
        _write_json(
            out,
            {"sessions": [session_summary_body(item) for item in await service.list_sessions()]},
        )
        return
    if command == "show":
        session_id = SessionId(cast(str, args.session_id))
        _write_json(out, session_body(await service.get_session(session_id)))
        return
    if command == "events":
        session_id = SessionId(cast(str, args.session_id))
        after = cast(str | None, args.after)
        limit = cast(int | None, args.limit)
        _write_json(
            out,
            event_batch_body(
                await service.stream_events(
                    session_id,
                    after_event_id=None if after is None else EventId(after),
                    limit=limit,
                )
            ),
        )
        return
    if command == "pause":
        session_id = SessionId(cast(str, args.session_id))
        run = await service.pause_session(session_id, reason=cast(str, args.reason))
        _write_json(out, runtime_run_body(run))
        return
    if command == "resume":
        session_id = SessionId(cast(str, args.session_id))
        run = await service.resume_session(
            session_id,
            confirmed=_optional_bool(cast(str | None, args.confirmed)),
        )
        _write_json(out, runtime_run_body(run))
        return
    if command == "cancel":
        session_id = SessionId(cast(str, args.session_id))
        run = await service.cancel_session(session_id, reason=cast(str, args.reason))
        _write_json(out, runtime_run_body(run))
        return
    raise ValueError(f"unknown session command: {command}")


def _write_json(out: TextIO, payload: object) -> None:
    json.dump(_json_safe(payload), out, indent=2, sort_keys=True)
    out.write("\n")


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe(item) for item in value]
    return str(value)


def _optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value == "true"


def _package_version() -> str:
    try:
        return version("universal-agent-runtime")
    except PackageNotFoundError:
        return "0.1.0"


if __name__ == "__main__":
    raise SystemExit(main())
