from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable, Sequence
from importlib.metadata import PackageNotFoundError, version
from inspect import isawaitable
from pathlib import Path
from typing import TextIO, cast

from universal_agent.agentd.app import AgentdApp
from universal_agent.agentd.http import AgentdAuthPolicy
from universal_agent.agentd.representations import (
    audit_records_body,
    capability_body,
    config_body,
    cost_body,
    distributed_cancellation_body,
    distributed_health_body,
    distributed_lock_lifecycle_body,
    distributed_maintenance_body,
    distributed_pending_action_scheduling_body,
    distributed_prune_body,
    distributed_scheduling_body,
    distributed_snapshot_body,
    distributed_worker_lifecycle_body,
    distributed_worker_run_batch_body,
    distributed_worker_run_body,
    doctor_body,
    domain_body,
    evaluator_body,
    event_batch_body,
    health_body,
    log_records_body,
    memory_body,
    metrics_body,
    multi_agent_body,
    policy_body,
    ready_body,
    runtime_run_body,
    session_batch_body,
    sse_event_batch_text,
    state_event_repair_body,
    tool_body,
    trace_spans_body,
)
from universal_agent.agentd.server import AgentdHttpServer, AgentdServerConfig
from universal_agent.agentd.session_representations import (
    session_body,
    session_evidence_body,
    session_explorer_body,
    session_world_body,
)
from universal_agent.cli_catalog_commands import (
    _dispatch_domain_packages,
    _dispatch_profile,
)
from universal_agent.cli_ecosystem import _dispatch_ecosystem
from universal_agent.cli_evaluation import _dispatch_eval
from universal_agent.cli_init import _dispatch_init
from universal_agent.cli_io import (
    CliExit,
    _optional_bool,
    _parse_optional_datetime,
    _success_criteria,
    _write_error,
    _write_json,
    _write_text,
)
from universal_agent.cli_parser import build_parser
from universal_agent.core import (
    ActionId,
    EventId,
    Goal,
    SessionId,
    Task,
    TaskId,
)
from universal_agent.core.config_validation import parse_bounded_float
from universal_agent.distributed import (
    DistributedLockConflictError,
    DistributedLockLeaseId,
    DistributedLockLeaseLostError,
    DistributedLockOwnerId,
    WorkerId,
    WorkerNotFoundError,
    WorkItemId,
    WorkItemNotFoundError,
)
from universal_agent.domain import (
    DomainPackageNotFoundError,
)
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
from universal_agent.evaluation.dataset import (
    EvaluationDatasetNotFoundError,
)
from universal_agent.host import build_configured_model_adapter
from universal_agent.profile import (
    ProfileConfigNotFoundError,
)
from universal_agent.runtime import (
    RuntimeEventBatch,
)
from universal_agent.security import EnvSecretProvider
from universal_agent.service import RuntimeService
from universal_agent.state import StateNotFoundError
from universal_agent.tui import build_tui_snapshot, render_tui_snapshot

__all__ = [
    "LOCAL_PROFILE_NAME",
    "build_configured_probe_service",
    "build_configured_service",
    "build_default_service",
    "main",
    "run_cli",
]

ServerRunner = Callable[[AgentdHttpServer], Awaitable[None] | None]


def build_default_service() -> RuntimeService:
    return build_kubernetes_default_service()


def build_configured_service(profile_config_path: str | Path) -> RuntimeService:
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
    runtime_service = service or _service_from_args(args)

    try:
        await _dispatch(args, runtime_service, out, server_runner=server_runner)
    except (
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
        if cast(str, args.format) == "prometheus":
            _write_text(out, await service.prometheus_metrics())
            return
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
    if command == "multi-agent":
        _write_json(out, multi_agent_body(service.multi_agent()))
        return
    if command == "repair":
        repair_command = cast(str, args.repair_command)
        if repair_command == "state-events":
            confirmed = cast(str, args.confirmed) == "true"
            _write_json(
                out,
                state_event_repair_body(
                    await service.repair_state_event_consistency(
                        confirmed=confirmed,
                        dry_run=cast(bool, args.dry_run),
                    )
                ),
            )
            return
        raise ValueError(f"unknown repair command: {repair_command}")
    if command == "distributed":
        distributed_command = cast(str, args.distributed_command)
        if distributed_command == "snapshot":
            snapshot = service.distributed_snapshot()
            if snapshot is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_snapshot_body(snapshot))
            return
        if distributed_command == "health":
            health = service.distributed_health()
            if health is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_health_body(health))
            return
        if distributed_command == "schedule-session":
            scheduling = service.distributed_schedule_session(
                SessionId(cast(str, args.session_id)),
                priority=cast(int, args.priority),
                max_attempts=cast(int, args.max_attempts),
            )
            if scheduling is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_scheduling_body(scheduling))
            return
        if distributed_command == "schedule-goal":
            profile = cast(str, args.profile)
            profile_error = service.profile_selection_error(profile)
            if profile_error is not None:
                raise ValueError(profile_error)
            criteria = _success_criteria(cast(list[str], args.success))
            goal = Goal(cast(str, args.goal), criteria)
            task = Task(cast(str, args.task), tuple(item.key for item in criteria))
            scheduling = service.distributed_schedule_goal(
                goal,
                task,
                priority=cast(int, args.priority),
                max_attempts=cast(int, args.max_attempts),
            )
            if scheduling is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_scheduling_body(scheduling))
            return
        if distributed_command == "schedule-task":
            scheduling = service.distributed_schedule_task(
                SessionId(cast(str, args.session_id)),
                TaskId(cast(str, args.task_id)),
                priority=cast(int, args.priority),
                max_attempts=cast(int, args.max_attempts),
            )
            if scheduling is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_scheduling_body(scheduling))
            return
        if distributed_command == "schedule-action":
            confirmed = cast(str, args.confirmed) == "true"
            if not confirmed:
                raise ValueError("distributed schedule-action requires --confirmed true")
            scheduling = service.distributed_schedule_action(
                SessionId(cast(str, args.session_id)),
                TaskId(cast(str, args.task_id)),
                ActionId(cast(str, args.action_id)),
                confirmed=confirmed,
                priority=cast(int, args.priority),
                max_attempts=cast(int, args.max_attempts),
            )
            if scheduling is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_scheduling_body(scheduling))
            return
        if distributed_command == "schedule-pending-actions":
            confirmed = cast(str, args.confirmed) == "true"
            if not confirmed:
                raise ValueError("distributed schedule-pending-actions requires --confirmed true")
            pending_scheduling = await service.distributed_schedule_pending_actions(
                confirmed=confirmed,
                priority=cast(int, args.priority),
                max_attempts=cast(int, args.max_attempts),
            )
            if pending_scheduling is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_pending_action_scheduling_body(pending_scheduling))
            return
        if distributed_command == "expire":
            maintenance = service.distributed_expire()
            if maintenance is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_maintenance_body(maintenance))
            return
        if distributed_command == "prune-terminal":
            pruned = service.distributed_prune_terminal_work_items(
                before=_parse_optional_datetime(cast(str | None, args.before))
            )
            if pruned is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_prune_body(pruned))
            return
        if distributed_command == "cancel":
            cancellation = service.distributed_cancel_work_item(
                WorkItemId(cast(str, args.work_item_id)),
                reason=cast(str, args.reason),
            )
            if cancellation is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_cancellation_body(cancellation))
            return
        if distributed_command == "worker-register":
            lifecycle = service.distributed_register_worker(
                WorkerId(cast(str, args.worker_id)),
                capabilities=tuple(cast(list[str], args.capability)),
                ttl_seconds=cast(float, args.ttl_seconds),
            )
            if lifecycle is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_worker_lifecycle_body(lifecycle))
            return
        if distributed_command == "worker-heartbeat":
            lifecycle = service.distributed_heartbeat_worker(
                WorkerId(cast(str, args.worker_id)),
                ttl_seconds=cast(float, args.ttl_seconds),
            )
            if lifecycle is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_worker_lifecycle_body(lifecycle))
            return
        if distributed_command == "worker-run-once":
            run = await service.distributed_run_worker_once(
                WorkerId(cast(str, args.worker_id)),
                lease_ttl_seconds=cast(float, args.lease_ttl_seconds),
                worker_ttl_seconds=cast(float, args.worker_ttl_seconds),
                heartbeat_interval_seconds=cast(float | None, args.heartbeat_interval_seconds),
            )
            if run is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_worker_run_body(run))
            return
        if distributed_command == "worker-run":
            runs = await service.distributed_run_worker_until_idle(
                WorkerId(cast(str, args.worker_id)),
                max_items=cast(int, args.max_items),
                lease_ttl_seconds=cast(float, args.lease_ttl_seconds),
                worker_ttl_seconds=cast(float, args.worker_ttl_seconds),
                heartbeat_interval_seconds=cast(float | None, args.heartbeat_interval_seconds),
            )
            if runs is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_worker_run_batch_body(runs))
            return
        if distributed_command == "worker-drain":
            lifecycle = service.distributed_drain_worker(
                WorkerId(cast(str, args.worker_id)),
                reason=cast(str, args.reason),
            )
            if lifecycle is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_worker_lifecycle_body(lifecycle))
            return
        if distributed_command == "worker-offline":
            lifecycle = service.distributed_mark_worker_offline(
                WorkerId(cast(str, args.worker_id)),
                reason=cast(str, args.reason),
            )
            if lifecycle is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_worker_lifecycle_body(lifecycle))
            return
        if distributed_command == "lock-acquire":
            lock_lifecycle = service.distributed_acquire_lock(
                lock_key=cast(str, args.lock_key),
                owner_id=DistributedLockOwnerId(cast(str, args.owner_id)),
                ttl_seconds=cast(float, args.ttl_seconds),
            )
            if lock_lifecycle is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_lock_lifecycle_body(lock_lifecycle))
            return
        if distributed_command == "lock-heartbeat":
            lock_lifecycle = service.distributed_heartbeat_lock(
                DistributedLockLeaseId(cast(str, args.lease_id)),
                owner_id=DistributedLockOwnerId(cast(str, args.owner_id)),
                ttl_seconds=cast(float, args.ttl_seconds),
            )
            if lock_lifecycle is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_lock_lifecycle_body(lock_lifecycle))
            return
        if distributed_command == "lock-release":
            lock_lifecycle = service.distributed_release_lock(
                DistributedLockLeaseId(cast(str, args.lease_id)),
                owner_id=DistributedLockOwnerId(cast(str, args.owner_id)),
            )
            if lock_lifecycle is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_lock_lifecycle_body(lock_lifecycle))
            return
        raise ValueError(f"unknown distributed command: {distributed_command}")
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
    if command == "memory":
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
    task = Task(cast(str, args.task), tuple(item.key for item in criteria))
    run = await service.run_goal(goal, task)
    _write_json(out, runtime_run_body(run))


async def _dispatch_tui(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    session_id = cast(str | None, args.session_id)
    snapshot = await build_tui_snapshot(
        service,
        session_id=None if session_id is None else SessionId(session_id),
        session_limit=cast(int, args.session_limit),
        event_limit=cast(int, args.event_limit),
    )
    _write_text(out, render_tui_snapshot(snapshot))


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


async def _dispatch_serve(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
    *,
    server_runner: ServerRunner | None = None,
) -> None:
    host = cast(str, args.host)
    port = cast(int, args.port)
    auth_token = _resolve_cli_auth_token(
        explicit=cast(str | None, args.auth_token),
        env_key=cast(str | None, args.auth_token_env),
        label="auth token",
    )
    read_only_auth_token = _resolve_cli_auth_token(
        explicit=cast(str | None, args.read_only_auth_token),
        env_key=cast(str | None, args.read_only_auth_token_env),
        label="read-only auth token",
    )
    try:
        server = AgentdHttpServer(
            AgentdApp(
                service,
                auth=AgentdAuthPolicy(
                    bearer_token=auth_token,
                    read_only_bearer_token=read_only_auth_token,
                ),
                evaluation_report_dir=cast(str | None, args.evaluation_report_dir),
            ),
            AgentdServerConfig(host=host, port=port),
        )
    except OSError as exc:
        raise ValueError(f"failed to bind agentd server on {host}:{port}: {exc}") from exc
    try:
        _write_json(
            out,
            {
                "status": "serving",
                "base_url": server.base_url,
                "host": host,
                "port": server.server_address[1],
                "auth_required": auth_token is not None or read_only_auth_token is not None,
                "read_only_auth_enabled": read_only_auth_token is not None,
                "evaluation_report_dir": cast(str | None, args.evaluation_report_dir),
            },
        )
        out.flush()
        result = (server_runner or _serve_forever)(server)
        if isawaitable(result):
            await result
    finally:
        server.server_close()


async def _serve_forever(server: AgentdHttpServer) -> None:
    await server.serve()


def _resolve_cli_auth_token(
    *,
    explicit: str | None,
    env_key: str | None,
    label: str,
) -> str | None:
    if explicit is not None and env_key is not None:
        raise ValueError(f"agentd {label} accepts either a literal value or env key, not both")
    if explicit is not None:
        return explicit
    if env_key is None:
        return None
    token = EnvSecretProvider().get_secret(env_key)
    if token is None:
        raise ValueError(f"agentd {label} env key is missing or empty: {env_key}")
    return token


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
    if command == "diagnostics":
        session_id = SessionId(cast(str, args.session_id))
        _write_json(out, session_explorer_body(await service.session_explorer(session_id)))
        return
    if command == "evidence":
        session_id = SessionId(cast(str, args.session_id))
        _write_json(out, session_evidence_body(await service.session_explorer(session_id)))
        return
    if command == "world":
        session_id = SessionId(cast(str, args.session_id))
        _write_json(
            out,
            session_world_body(
                await service.session_world(
                    session_id,
                    entity_id=cast(str | None, args.entity),
                    relation=cast(str | None, args.relation),
                )
            ),
        )
        return
    if command == "events":
        session_id = SessionId(cast(str, args.session_id))
        after = cast(str | None, args.after)
        limit = cast(int | None, args.limit)
        batch = await _stream_events_for_cli(
            service,
            session_id,
            after_event_id=None if after is None else EventId(after),
            limit=limit,
            wait=cast(bool, args.wait),
            timeout_seconds=cast(float, args.timeout_seconds),
            poll_interval_seconds=cast(float, args.poll_interval_seconds),
        )
        if cast(str, args.format) == "sse":
            _write_text(out, sse_event_batch_text(batch))
            return
        _write_json(out, event_batch_body(batch))
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


async def _stream_events_for_cli(
    service: RuntimeService,
    session_id: SessionId,
    *,
    after_event_id: EventId | None,
    limit: int | None,
    wait: bool,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> RuntimeEventBatch:
    if not wait:
        return await service.stream_events(
            session_id,
            after_event_id=after_event_id,
            limit=limit,
        )
    timeout_seconds = parse_bounded_float(
        timeout_seconds,
        "timeout_seconds",
        minimum=0.0,
        maximum=30.0,
    )
    poll_interval_seconds = parse_bounded_float(
        poll_interval_seconds,
        "poll_interval_seconds",
        minimum=0.001,
        maximum=5.0,
    )

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    batch = await service.stream_events(
        session_id,
        after_event_id=after_event_id,
        limit=limit,
    )
    while not batch.events and loop.time() < deadline:
        await asyncio.sleep(min(poll_interval_seconds, max(0.0, deadline - loop.time())))
        batch = await service.stream_events(
            session_id,
            after_event_id=after_event_id,
            limit=limit,
        )
    return batch


def _package_version() -> str:
    try:
        return version("universal-agent-runtime")
    except PackageNotFoundError:
        return "0.1.0"


if __name__ == "__main__":
    raise SystemExit(main())
