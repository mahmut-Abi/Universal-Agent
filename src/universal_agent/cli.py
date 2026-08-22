from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TextIO, cast

from universal_agent.agentd.app import (
    AgentdApp,
    audit_records_body,
    capability_body,
    config_body,
    cost_body,
    doctor_body,
    domain_body,
    event_batch_body,
    health_body,
    log_records_body,
    metrics_body,
    profile_body,
    ready_body,
    runtime_run_body,
    session_batch_body,
    session_body,
    tool_body,
    trace_spans_body,
)
from universal_agent.agentd.server import AgentdHttpServer, AgentdServerConfig
from universal_agent.core import (
    Decision,
    DecisionType,
    EventId,
    Goal,
    JsonMapping,
    SessionId,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.domain import DomainLoader, RuntimeBuilder
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.host import DomainConfig, RuntimeConfig
from universal_agent.model import ScriptedModelAdapter
from universal_agent.profile import AgentProfile
from universal_agent.runtime import AgentRuntime, InMemoryEventSink, RuntimeAPI
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore, StateNotFoundError

LOCAL_PROFILE_NAME = "local-kubernetes"
ServerRunner = Callable[[AgentdHttpServer], None]


def _local_domain() -> DomainConfig:
    return DomainConfig("kubernetes", "0.2.0")


def _default_decisions() -> tuple[Decision, ...]:
    return (
        Decision(
            DecisionType.EXECUTE,
            "Inspect workload from local CLI profile",
            capability="inspect_workload",
            target="deployment/example",
            arguments=immutable_json({"name": "example"}),
            expected_observations=("healthy",),
        ),
        Decision(DecisionType.FINISH, "Local CLI profile verified workload health"),
    )


def _default_profile() -> AgentProfile:
    domain = _local_domain()
    return AgentProfile(
        LOCAL_PROFILE_NAME,
        "0.1.0",
        "Local fake-backed Kubernetes profile",
        domain,
        RuntimeConfig(environment=immutable_json({"environment": "local"}), domain=domain),
        (domain,),
    )


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
    profile = _default_profile()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(_default_decisions()),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "local"}),
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
        profiles=(profile,),
        config=profile.runtime,
    )


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
    runtime_service = service or build_default_service()

    try:
        await _dispatch(args, runtime_service, out, server_runner=server_runner)
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
    commands.add_parser("metrics")
    commands.add_parser("cost")
    commands.add_parser("logs")
    traces = commands.add_parser("traces")
    traces.add_argument("--format", choices=("runtime", "otlp"), default="runtime")
    commands.add_parser("doctor")
    commands.add_parser("audit")

    init = commands.add_parser("init")
    init.add_argument("--output", default="profile.json")
    init.add_argument("--profile", default=LOCAL_PROFILE_NAME)
    init.add_argument("--environment", default="local")
    init.add_argument("--store-path", default=".universal-agent/store")
    init.add_argument("--force", action="store_true")

    config = commands.add_parser("config")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("show")

    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    run = commands.add_parser("run")
    run.add_argument("profile")
    run.add_argument("goal")
    run.add_argument("--task", default="Run goal")

    domain = commands.add_parser("domain")
    domain_commands = domain.add_subparsers(dest="domain_command", required=True)
    domain_commands.add_parser("list")

    profile = commands.add_parser("profile")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_commands.add_parser("list")
    profile_show = profile_commands.add_parser("show")
    profile_show.add_argument("profile")

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

    list_sessions = session_commands.add_parser("list")
    list_sessions.add_argument("--after")
    list_sessions.add_argument("--limit", type=int)

    show = session_commands.add_parser("show")
    show.add_argument("session_id")

    events = session_commands.add_parser("events")
    events.add_argument("session_id")
    events.add_argument("--after")
    events.add_argument("--limit", type=int)

    audit = session_commands.add_parser("audit")
    audit.add_argument("session_id")

    cost = session_commands.add_parser("cost")
    cost.add_argument("session_id")

    logs = session_commands.add_parser("logs")
    logs.add_argument("session_id")

    traces = session_commands.add_parser("traces")
    traces.add_argument("session_id")
    traces.add_argument("--format", choices=("runtime", "otlp"), default="runtime")

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
    *,
    server_runner: ServerRunner | None = None,
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
    if command == "metrics":
        _write_json(out, metrics_body(await service.metrics()))
        return
    if command == "cost":
        _write_json(out, cost_body(await service.cost()))
        return
    if command == "logs":
        _write_json(out, log_records_body(await service.logs()))
        return
    if command == "traces":
        if cast(str, args.format) == "otlp":
            _write_json(out, await service.opentelemetry_traces())
            return
        _write_json(out, trace_spans_body(await service.traces()))
        return
    if command == "doctor":
        _write_json(out, doctor_body(await service.doctor()))
        return
    if command == "audit":
        _write_json(out, audit_records_body(await service.audit_records()))
        return
    if command == "init":
        _dispatch_init(args, out)
        return
    if command == "config":
        _dispatch_config(args, service, out)
        return
    if command == "serve":
        _dispatch_serve(args, service, out, server_runner=server_runner)
        return
    if command == "run":
        await _dispatch_run(args, service, out)
        return
    if command == "domain":
        _write_json(out, {"domains": [domain_body(item) for item in service.domains()]})
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
    goal = Goal(cast(str, args.goal), (SuccessCriterion("healthy", True),))
    task = Task(cast(str, args.task), ("healthy",))
    run = await service.run_goal(goal, task)
    _write_json(out, runtime_run_body(run))


def _dispatch_profile(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    command = cast(str, args.profile_command)
    if command == "list":
        _write_json(out, {"profiles": [profile_body(item) for item in service.profiles()]})
        return
    if command == "show":
        profile = cast(str, args.profile)
        if not service.accepts_profile(profile):
            raise ValueError(f"unknown profile: {profile}")
        _write_json(out, profile_body(service.profile(profile)))
        return
    raise ValueError(f"unknown profile command: {command}")


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


def _dispatch_init(args: argparse.Namespace, out: TextIO) -> None:
    output = Path(cast(str, args.output))
    if output.exists() and not cast(bool, args.force):
        raise ValueError(f"profile config already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    profile_name = cast(str, args.profile)
    payload = _profile_config_payload(
        profile_name=profile_name,
        environment=cast(str, args.environment),
        store_path=cast(str, args.store_path),
    )
    tmp_path = output.with_name(output.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(output)
    _write_json(out, {"status": "created", "profile": profile_name, "path": str(output)})


def _profile_config_payload(
    *,
    profile_name: str,
    environment: str,
    store_path: str,
) -> dict[str, object]:
    domain = {"name": "kubernetes", "version": "0.2.0"}
    return {
        "name": profile_name,
        "version": "0.1.0",
        "description": "Local fake-backed Kubernetes profile",
        "domain": domain,
        "runtime": {
            "environment": {"environment": environment},
            "store": {"backend": "file", "path": store_path},
            "limits": {"max_iterations": 20, "max_recovery_steps": 8},
            "domain": domain,
        },
    }


def _dispatch_serve(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
    *,
    server_runner: ServerRunner | None = None,
) -> None:
    host = cast(str, args.host)
    port = cast(int, args.port)
    server = AgentdHttpServer(
        AgentdApp(service),
        AgentdServerConfig(host=host, port=port),
    )
    try:
        _write_json(
            out,
            {
                "status": "serving",
                "base_url": server.base_url,
                "host": host,
                "port": server.server_address[1],
            },
        )
        out.flush()
        (server_runner or _serve_forever)(server)
    finally:
        server.server_close()


def _serve_forever(server: AgentdHttpServer) -> None:
    server.serve_forever()


async def _dispatch_session(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    command = cast(str, args.session_command)
    if command == "list":
        after = cast(str | None, args.after)
        limit = cast(int | None, args.limit)
        _write_json(
            out,
            session_batch_body(
                await service.stream_sessions(
                    after_session_id=None if after is None else SessionId(after),
                    limit=limit,
                )
            ),
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
    if command == "audit":
        session_id = SessionId(cast(str, args.session_id))
        _write_json(out, audit_records_body(await service.audit_records(session_id)))
        return
    if command == "cost":
        session_id = SessionId(cast(str, args.session_id))
        _write_json(out, cost_body(await service.cost(session_id)))
        return
    if command == "logs":
        session_id = SessionId(cast(str, args.session_id))
        _write_json(out, log_records_body(await service.logs(session_id)))
        return
    if command == "traces":
        session_id = SessionId(cast(str, args.session_id))
        if cast(str, args.format) == "otlp":
            _write_json(out, await service.opentelemetry_traces(session_id))
            return
        _write_json(out, trace_spans_body(await service.traces(session_id)))
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
