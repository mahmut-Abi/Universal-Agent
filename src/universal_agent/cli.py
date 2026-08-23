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
    distributed_cancellation_body,
    distributed_health_body,
    distributed_lock_lifecycle_body,
    distributed_maintenance_body,
    distributed_scheduling_body,
    distributed_snapshot_body,
    distributed_worker_lifecycle_body,
    doctor_body,
    domain_body,
    evaluator_body,
    event_batch_body,
    health_body,
    log_records_body,
    memory_body,
    metrics_body,
    policy_body,
    profile_body,
    ready_body,
    runtime_run_body,
    session_batch_body,
    session_body,
    session_evidence_body,
    session_explorer_body,
    session_world_body,
    sse_event_batch_text,
    tool_body,
    trace_spans_body,
)
from universal_agent.agentd.server import AgentdHttpServer, AgentdServerConfig
from universal_agent.core import (
    Decision,
    DecisionType,
    ErrorCode,
    EventId,
    ExecutionStatus,
    Goal,
    JsonMapping,
    SessionId,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.distributed import (
    DistributedLockConflictError,
    DistributedLockLeaseId,
    DistributedLockLeaseLostError,
    DistributedLockOwnerId,
    DistributedRuntimeCoordinator,
    WorkerId,
    WorkerNotFoundError,
    WorkItemId,
    WorkItemNotFoundError,
)
from universal_agent.domain import DomainLoader, RuntimeBuilder
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.evaluation.console import (
    build_evaluation_console_snapshot,
    render_evaluation_console,
)
from universal_agent.evaluation.harness import (
    EvaluationQualityGate,
    EvaluationScenario,
    EvaluationScenarioKind,
    EvaluationScenarioSelector,
    EvaluationSuite,
    ScenarioExpectations,
)
from universal_agent.evaluation.recording import (
    EvaluationCheckRecording,
    EvaluationGateRecording,
    EvaluationReportComparison,
    EvaluationReportComparisonCheck,
    EvaluationReportRecording,
    EvaluationScenarioRecording,
    EvaluationSummaryRecording,
    FileEvaluationReportStore,
    FileReplayRecordingStore,
    ReplayRecordingNotFoundError,
    compare_evaluation_reports,
    decode_evaluation_report,
    encode_evaluation_junit_xml,
    encode_replay_recording,
    json_mapping,
)
from universal_agent.evaluation.replay import (
    DeterministicReplayHarness,
    ReplayCheck,
    ReplayRecording,
    ReplayReport,
)
from universal_agent.evaluation.runner import EvaluationRunner, EvaluationRunResult
from universal_agent.evaluation.scenario_config import (
    EvaluationSuiteConfig,
    load_evaluation_suite_config,
)
from universal_agent.host import DomainConfig, RuntimeConfig, RuntimeHost
from universal_agent.model import ScriptedModelAdapter
from universal_agent.profile import AgentProfile, ProfileConfig
from universal_agent.runtime import AgentRuntime, InMemoryEventSink, RuntimeAPI
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore, StateNotFoundError
from universal_agent.tui import build_tui_snapshot, render_tui_snapshot

LOCAL_PROFILE_NAME = "local-kubernetes"
ServerRunner = Callable[[AgentdHttpServer], None]


class CliExit(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


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
        Decision(
            DecisionType.EXECUTE,
            "Attempt invalid scale from local CLI evaluation profile",
            capability="scale_workload",
            target="deployment/example",
            arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 0}),
            expected_observations=("mutation_applied",),
        ),
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
        distributed_coordinator=DistributedRuntimeCoordinator(),
    )


def build_configured_service(profile_config_path: str | Path) -> RuntimeService:
    profile = ProfileConfig.from_json_file(profile_config_path).to_profile()
    backend = _DefaultCliBackend()
    host = RuntimeHost.from_profile(
        profile=profile,
        model=ScriptedModelAdapter(_default_decisions()),
        domain=KubernetesRemediationDomain(backend, backend),
    )
    return host.service


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
    return asyncio.run(run_cli(argv))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent")
    parser.add_argument(
        "--profile-config",
        help="Load an Agent Profile JSON config before dispatching the command.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("version")
    commands.add_parser("health")
    commands.add_parser("ready")
    metrics = commands.add_parser("metrics")
    metrics.add_argument("--format", choices=("json", "prometheus"), default="json")
    commands.add_parser("cost")
    commands.add_parser("logs")
    traces = commands.add_parser("traces")
    traces.add_argument("--format", choices=("runtime", "otlp"), default="runtime")
    commands.add_parser("doctor")
    commands.add_parser("audit")

    distributed = commands.add_parser("distributed")
    distributed_commands = distributed.add_subparsers(
        dest="distributed_command",
        required=True,
    )
    distributed_commands.add_parser("snapshot")
    distributed_commands.add_parser("health")
    distributed_commands.add_parser("expire")
    distributed_schedule = distributed_commands.add_parser("schedule-session")
    distributed_schedule.add_argument("session_id")
    distributed_schedule.add_argument("--priority", type=int, default=0)
    distributed_schedule.add_argument("--max-attempts", type=int, default=3)
    distributed_cancel = distributed_commands.add_parser("cancel")
    distributed_cancel.add_argument("work_item_id")
    distributed_cancel.add_argument(
        "--reason",
        default="distributed work item cancelled from CLI",
    )
    distributed_register = distributed_commands.add_parser("worker-register")
    distributed_register.add_argument("worker_id")
    distributed_register.add_argument("--capability", action="append", default=[])
    distributed_register.add_argument("--ttl-seconds", type=float, default=30.0)

    distributed_heartbeat = distributed_commands.add_parser("worker-heartbeat")
    distributed_heartbeat.add_argument("worker_id")
    distributed_heartbeat.add_argument("--ttl-seconds", type=float, default=30.0)

    distributed_drain = distributed_commands.add_parser("worker-drain")
    distributed_drain.add_argument("worker_id")
    distributed_drain.add_argument("--reason", default="worker draining from CLI")

    distributed_offline = distributed_commands.add_parser("worker-offline")
    distributed_offline.add_argument("worker_id")
    distributed_offline.add_argument("--reason", default="worker offline from CLI")

    distributed_lock_acquire = distributed_commands.add_parser("lock-acquire")
    distributed_lock_acquire.add_argument("lock_key")
    distributed_lock_acquire.add_argument("--owner-id", required=True)
    distributed_lock_acquire.add_argument("--ttl-seconds", type=float, default=30.0)

    distributed_lock_heartbeat = distributed_commands.add_parser("lock-heartbeat")
    distributed_lock_heartbeat.add_argument("lease_id")
    distributed_lock_heartbeat.add_argument("--owner-id", required=True)
    distributed_lock_heartbeat.add_argument("--ttl-seconds", type=float, default=30.0)

    distributed_lock_release = distributed_commands.add_parser("lock-release")
    distributed_lock_release.add_argument("lease_id")
    distributed_lock_release.add_argument("--owner-id", required=True)

    init = commands.add_parser("init")
    init.add_argument("--output", default="profile.json")
    init.add_argument("--profile", default=LOCAL_PROFILE_NAME)
    init.add_argument("--environment", default="local")
    init.add_argument("--store-backend", choices=("memory", "file", "sqlite"), default="file")
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

    tui = commands.add_parser("tui")
    tui.add_argument("--session-id")
    tui.add_argument("--session-limit", type=int, default=5)
    tui.add_argument("--event-limit", type=int, default=12)

    evaluate = commands.add_parser("eval")
    eval_commands = evaluate.add_subparsers(dest="eval_command", required=True)

    eval_list = eval_commands.add_parser("list")
    eval_list.add_argument("profile")
    eval_list.add_argument("--suite", default="local evaluation suite")
    eval_list.add_argument("--suite-file")
    _add_evaluation_selector_arguments(eval_list)

    eval_run = eval_commands.add_parser("run")
    eval_run.add_argument("profile")
    eval_run.add_argument("--suite", default="local evaluation suite")
    eval_run.add_argument("--suite-file")
    eval_run.add_argument("--report-dir")
    eval_run.add_argument("--format", choices=("json", "junit"), default="json")
    eval_run.add_argument("--min-pass-rate", type=float)
    eval_run.add_argument("--min-goal-completion-rate", type=float)
    eval_run.add_argument("--min-task-success-rate", type=float)
    eval_run.add_argument("--min-action-success-rate", type=float)
    eval_run.add_argument("--max-tool-failure-rate", type=float)
    eval_run.add_argument("--max-policy-denial-rate", type=float)
    eval_run.add_argument("--max-average-recoveries", type=float)
    eval_run.add_argument("--max-human-intervention-rate", type=float)
    eval_run.add_argument("--max-average-actions", type=float)
    eval_run.add_argument("--max-average-active-resource-locks", type=float)
    eval_run.add_argument("--max-average-duration-ms", type=float)
    eval_run.add_argument("--max-average-model-calls", type=float)
    eval_run.add_argument("--max-average-model-tokens", type=float)
    eval_run.add_argument("--max-resource-conflict-rate", type=float)
    eval_run.add_argument("--max-total-model-cost-micros", type=int)
    eval_run.add_argument("--fail-on-fail", action="store_true")
    _add_evaluation_selector_arguments(eval_run)

    eval_replay = eval_commands.add_parser("replay")
    eval_replay.add_argument("profile")
    eval_replay.add_argument("--suite", default="local evaluation suite")
    eval_replay.add_argument("--suite-file")
    eval_replay.add_argument("--recording-dir", required=True)
    eval_replay.add_argument("--update", action="store_true")
    eval_replay.add_argument("--fail-on-fail", action="store_true")
    _add_evaluation_selector_arguments(eval_replay)

    eval_recordings = eval_commands.add_parser("recordings")
    eval_recordings.add_argument("--recording-dir", required=True)

    eval_compare = eval_commands.add_parser("compare")
    eval_compare.add_argument("expected")
    eval_compare.add_argument("actual")
    eval_compare.add_argument("--fail-on-fail", action="store_true")

    eval_reports = eval_commands.add_parser("reports")
    eval_reports.add_argument("--report-dir", required=True)

    eval_console = eval_commands.add_parser("console")
    eval_console.add_argument("--report-dir", required=True)

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

    policies = commands.add_parser("policies")
    policies_commands = policies.add_subparsers(dest="policies_command", required=True)
    policies_commands.add_parser("list")

    evaluators = commands.add_parser("evaluators")
    evaluators_commands = evaluators.add_subparsers(dest="evaluators_command", required=True)
    evaluators_commands.add_parser("list")

    memory = commands.add_parser("memory")
    memory_commands = memory.add_subparsers(dest="memory_command", required=True)
    memory_commands.add_parser("list")

    session = commands.add_parser("session")
    session_commands = session.add_subparsers(dest="session_command", required=True)

    list_sessions = session_commands.add_parser("list")
    list_sessions.add_argument("--after")
    list_sessions.add_argument("--limit", type=int)

    show = session_commands.add_parser("show")
    show.add_argument("session_id")

    diagnostics = session_commands.add_parser("diagnostics")
    diagnostics.add_argument("session_id")

    evidence = session_commands.add_parser("evidence")
    evidence.add_argument("session_id")

    world = session_commands.add_parser("world")
    world.add_argument("session_id")

    events = session_commands.add_parser("events")
    events.add_argument("session_id")
    events.add_argument("--after")
    events.add_argument("--limit", type=int)
    events.add_argument("--format", choices=("json", "sse"), default="json")

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


def _service_from_args(args: argparse.Namespace) -> RuntimeService:
    profile_config = cast(str | None, args.profile_config)
    if profile_config is None:
        return build_default_service()
    return build_configured_service(profile_config)


def _add_evaluation_selector_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--kind",
        action="append",
        choices=tuple(item.value for item in EvaluationScenarioKind),
    )
    command.add_argument("--tag", action="append")
    command.add_argument("--exclude-tag", action="append")


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
        if distributed_command == "expire":
            maintenance = service.distributed_expire()
            if maintenance is None:
                raise ValueError("distributed runtime coordinator is not configured")
            _write_json(out, distributed_maintenance_body(maintenance))
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
        _dispatch_serve(args, service, out, server_runner=server_runner)
        return
    if command == "run":
        await _dispatch_run(args, service, out)
        return
    if command == "tui":
        await _dispatch_tui(args, service, out)
        return
    if command == "eval":
        await _dispatch_eval(args, service, out)
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
    goal = Goal(cast(str, args.goal), (SuccessCriterion("healthy", True),))
    task = Task(cast(str, args.task), ("healthy",))
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


async def _dispatch_eval(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    command = cast(str, args.eval_command)
    if command == "list":
        profile = cast(str, args.profile)
        if not service.accepts_profile(profile):
            raise ValueError(f"unknown profile: {profile}")
        suite = _evaluation_suite(args)
        scenarios = suite.select(_evaluation_selector(args))
        _write_json(out, _evaluation_list_body(suite, scenarios))
        return
    if command == "run":
        profile = cast(str, args.profile)
        if not service.accepts_profile(profile):
            raise ValueError(f"unknown profile: {profile}")
        report_dir = cast(str | None, args.report_dir)
        suite_config = _evaluation_suite_config(args)
        result = await EvaluationRunner(
            service,
            report_store=None if report_dir is None else FileEvaluationReportStore(report_dir),
        ).run_suite(
            suite_config.suite,
            selector=_evaluation_selector(args),
            gate=_evaluation_quality_gate(args, suite_config.quality_gate),
        )
        if cast(str, args.format) == "junit":
            _write_text(out, encode_evaluation_junit_xml(result.recording))
            _write_text(out, "\n")
        else:
            _write_json(out, _evaluation_run_body(result, report_dir))
        if cast(bool, args.fail_on_fail) and not result.passed:
            raise CliExit(1)
        return
    if command == "replay":
        payload = await _dispatch_eval_replay(args, service)
        _write_json(out, payload)
        if cast(bool, args.fail_on_fail) and not cast(bool, payload["passed"]):
            raise CliExit(1)
        return
    if command == "recordings":
        recording_dir = cast(str, args.recording_dir)
        recordings = FileReplayRecordingStore(recording_dir).list_recordings()
        _write_json(out, _replay_recordings_body(recording_dir, recordings))
        return
    if command == "compare":
        comparison = compare_evaluation_reports(
            _load_evaluation_report(Path(cast(str, args.expected))),
            _load_evaluation_report(Path(cast(str, args.actual))),
        )
        _write_json(out, _evaluation_comparison_body(comparison))
        if cast(bool, args.fail_on_fail) and not comparison.passed:
            raise CliExit(1)
        return
    if command == "reports":
        report_dir = cast(str, args.report_dir)
        reports = FileEvaluationReportStore(report_dir).list_reports()
        _write_json(out, _evaluation_reports_body(report_dir, reports))
        return
    if command == "console":
        report_dir = cast(str, args.report_dir)
        _write_text(
            out,
            render_evaluation_console(build_evaluation_console_snapshot(report_dir)),
        )
        return
    raise ValueError(f"unknown eval command: {command}")


def _evaluation_selector(args: argparse.Namespace) -> EvaluationScenarioSelector | None:
    kinds = cast(list[str] | None, args.kind)
    tags = cast(list[str] | None, args.tag)
    exclude_tags = cast(list[str] | None, args.exclude_tag)
    if kinds is None and tags is None and exclude_tags is None:
        return None
    return EvaluationScenarioSelector(
        kinds=None if kinds is None else tuple(EvaluationScenarioKind(item) for item in kinds),
        tags=tuple(tags or ()),
        exclude_tags=tuple(exclude_tags or ()),
    )


async def _dispatch_eval_replay(
    args: argparse.Namespace,
    service: RuntimeService,
) -> dict[str, object]:
    profile = cast(str, args.profile)
    if not service.accepts_profile(profile):
        raise ValueError(f"unknown profile: {profile}")
    suite = _evaluation_suite(args)
    scenarios = suite.select(_evaluation_selector(args))
    if not scenarios:
        raise ValueError("evaluation replay selected no scenarios")

    recording_dir = cast(str, args.recording_dir)
    store = FileReplayRecordingStore(recording_dir)
    harness = DeterministicReplayHarness(service)
    if cast(bool, args.update):
        recordings = []
        for scenario in scenarios:
            recording = await harness.record(scenario)
            store.save(recording)
            recordings.append(recording)
        return {
            "mode": "record",
            "passed": True,
            "suite_name": suite.name,
            "recording_dir": recording_dir,
            "scenario_count": len(recordings),
            "scenarios": [encode_replay_recording(item) for item in recordings],
        }

    reports = []
    for scenario in scenarios:
        try:
            expected = store.load(scenario.name)
        except ReplayRecordingNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        reports.append(await harness.replay(scenario, expected))
    return {
        "mode": "replay",
        "passed": all(report.passed for report in reports),
        "suite_name": suite.name,
        "recording_dir": recording_dir,
        "scenario_count": len(reports),
        "scenarios": [_replay_report_body(report) for report in reports],
    }


def _evaluation_suite(args: argparse.Namespace) -> EvaluationSuite:
    return _evaluation_suite_config(args).suite


def _evaluation_suite_config(args: argparse.Namespace) -> EvaluationSuiteConfig:
    suite_file = cast(str | None, args.suite_file)
    if suite_file is not None:
        return load_evaluation_suite_config(suite_file)
    return EvaluationSuiteConfig(_local_evaluation_suite(cast(str, args.suite)))


def _evaluation_quality_gate(
    args: argparse.Namespace,
    suite_gate: EvaluationQualityGate | None,
) -> EvaluationQualityGate | None:
    overrides = {
        "min_pass_rate": cast(float | None, args.min_pass_rate),
        "min_goal_completion_rate": cast(float | None, args.min_goal_completion_rate),
        "min_task_success_rate": cast(float | None, args.min_task_success_rate),
        "min_action_success_rate": cast(float | None, args.min_action_success_rate),
        "max_tool_failure_rate": cast(float | None, args.max_tool_failure_rate),
        "max_policy_denial_rate": cast(float | None, args.max_policy_denial_rate),
        "max_average_recoveries_per_scenario": cast(
            float | None,
            args.max_average_recoveries,
        ),
        "max_human_intervention_rate": cast(float | None, args.max_human_intervention_rate),
        "max_average_actions_per_scenario": cast(float | None, args.max_average_actions),
        "max_average_active_resource_locks_per_scenario": cast(
            float | None,
            args.max_average_active_resource_locks,
        ),
        "max_average_execution_duration_ms_per_scenario": cast(
            float | None,
            args.max_average_duration_ms,
        ),
        "max_average_model_calls_per_scenario": cast(float | None, args.max_average_model_calls),
        "max_average_model_tokens_per_scenario": cast(
            float | None,
            args.max_average_model_tokens,
        ),
        "max_resource_conflict_rate": cast(float | None, args.max_resource_conflict_rate),
    }
    cost_override = cast(int | None, args.max_total_model_cost_micros)
    if (
        suite_gate is None
        and cost_override is None
        and all(value is None for value in overrides.values())
    ):
        return None
    base = EvaluationQualityGate() if suite_gate is None else suite_gate
    return EvaluationQualityGate(
        min_pass_rate=overrides["min_pass_rate"]
        if overrides["min_pass_rate"] is not None
        else base.min_pass_rate,
        min_goal_completion_rate=overrides["min_goal_completion_rate"]
        if overrides["min_goal_completion_rate"] is not None
        else base.min_goal_completion_rate,
        min_task_success_rate=overrides["min_task_success_rate"]
        if overrides["min_task_success_rate"] is not None
        else base.min_task_success_rate,
        min_action_success_rate=overrides["min_action_success_rate"]
        if overrides["min_action_success_rate"] is not None
        else base.min_action_success_rate,
        max_tool_failure_rate=overrides["max_tool_failure_rate"]
        if overrides["max_tool_failure_rate"] is not None
        else base.max_tool_failure_rate,
        max_policy_denial_rate=overrides["max_policy_denial_rate"]
        if overrides["max_policy_denial_rate"] is not None
        else base.max_policy_denial_rate,
        max_average_recoveries_per_scenario=overrides["max_average_recoveries_per_scenario"]
        if overrides["max_average_recoveries_per_scenario"] is not None
        else base.max_average_recoveries_per_scenario,
        max_human_intervention_rate=overrides["max_human_intervention_rate"]
        if overrides["max_human_intervention_rate"] is not None
        else base.max_human_intervention_rate,
        max_resource_conflict_rate=overrides["max_resource_conflict_rate"]
        if overrides["max_resource_conflict_rate"] is not None
        else base.max_resource_conflict_rate,
        max_average_active_resource_locks_per_scenario=overrides[
            "max_average_active_resource_locks_per_scenario"
        ]
        if overrides["max_average_active_resource_locks_per_scenario"] is not None
        else base.max_average_active_resource_locks_per_scenario,
        max_average_actions_per_scenario=overrides["max_average_actions_per_scenario"]
        if overrides["max_average_actions_per_scenario"] is not None
        else base.max_average_actions_per_scenario,
        max_average_execution_duration_ms_per_scenario=overrides[
            "max_average_execution_duration_ms_per_scenario"
        ]
        if overrides["max_average_execution_duration_ms_per_scenario"] is not None
        else base.max_average_execution_duration_ms_per_scenario,
        max_average_model_calls_per_scenario=overrides["max_average_model_calls_per_scenario"]
        if overrides["max_average_model_calls_per_scenario"] is not None
        else base.max_average_model_calls_per_scenario,
        max_average_model_tokens_per_scenario=overrides["max_average_model_tokens_per_scenario"]
        if overrides["max_average_model_tokens_per_scenario"] is not None
        else base.max_average_model_tokens_per_scenario,
        max_total_model_estimated_cost_micros=cost_override
        if cost_override is not None
        else base.max_total_model_estimated_cost_micros,
    )


def _local_evaluation_suite(name: str) -> EvaluationSuite:
    goal = Goal("Evaluate workload health", (SuccessCriterion("healthy", True),))
    task = Task("Inspect workload", ("healthy",))
    return EvaluationSuite(
        name,
        (
            EvaluationScenario(
                "healthy workload",
                goal,
                task,
                ScenarioExpectations(
                    expected_status=ExecutionStatus.COMPLETED,
                    expected_criteria=immutable_json({"healthy": True}),
                    required_events=("GoalCompleted", "EvaluationCompleted"),
                    required_evidence_claims=("healthy",),
                    required_capabilities=("inspect_workload",),
                    max_actions=1,
                ),
                kind=EvaluationScenarioKind.REGRESSION,
                tags=("smoke", "kubernetes"),
            ),
            EvaluationScenario(
                "invalid scale policy",
                goal,
                task,
                ScenarioExpectations(
                    expected_status=ExecutionStatus.FAILED,
                    expected_error_code=ErrorCode.POLICY_DENIED,
                    forbidden_events=("ActionStarted",),
                    required_audit_capabilities=("scale_workload",),
                    policy_denial_count=1,
                    max_actions=0,
                ),
                kind=EvaluationScenarioKind.POLICY,
                tags=("policy", "kubernetes"),
            ),
        ),
        tags=("local", "kubernetes"),
    )


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
        store_backend=cast(str, args.store_backend),
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
    store_backend: str,
    store_path: str,
) -> dict[str, object]:
    domain = {"name": "kubernetes", "version": "0.2.0"}
    store: dict[str, str] = {"backend": store_backend}
    if store_backend != "memory":
        store["path"] = store_path
    return {
        "name": profile_name,
        "version": "0.1.0",
        "description": "Local fake-backed Kubernetes profile",
        "domain": domain,
        "runtime": {
            "environment": {"environment": environment},
            "store": store,
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
        _write_json(out, session_world_body(await service.session_explorer(session_id)))
        return
    if command == "events":
        session_id = SessionId(cast(str, args.session_id))
        after = cast(str | None, args.after)
        limit = cast(int | None, args.limit)
        batch = await service.stream_events(
            session_id,
            after_event_id=None if after is None else EventId(after),
            limit=limit,
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


def _load_evaluation_report(path: Path) -> EvaluationReportRecording:
    with path.open("r", encoding="utf-8") as handle:
        return decode_evaluation_report(json_mapping(json.load(handle)))


def _evaluation_run_body(
    result: EvaluationRunResult,
    report_dir: str | None,
) -> dict[str, object]:
    return {
        "passed": result.passed,
        "suite": _evaluation_report_body(result.recording),
        "gate": (
            None if result.recording.gate is None else _evaluation_gate_body(result.recording.gate)
        ),
        "report_dir": report_dir,
    }


def _evaluation_reports_body(
    report_dir: str,
    reports: tuple[EvaluationReportRecording, ...],
) -> dict[str, object]:
    return {
        "report_dir": report_dir,
        "report_count": len(reports),
        "reports": [_evaluation_report_summary_body(item) for item in reports],
    }


def _evaluation_report_summary_body(recording: EvaluationReportRecording) -> dict[str, object]:
    return {
        "suite_name": recording.suite_name,
        "passed": recording.passed,
        "scenario_count": recording.summary.scenario_count,
        "passed_count": recording.summary.passed_count,
        "failed_count": recording.summary.failed_count,
        "gate_passed": None if recording.gate is None else recording.gate.passed,
        "failed_scenarios": [
            scenario.scenario_name for scenario in recording.scenarios if not scenario.passed
        ],
        "execution_duration_ms": recording.summary.execution_duration_ms,
        "model_total_token_count": recording.summary.model_total_token_count,
        "model_estimated_cost_micros": recording.summary.model_estimated_cost_micros,
    }


def _evaluation_list_body(
    suite: EvaluationSuite,
    scenarios: tuple[EvaluationScenario, ...],
) -> dict[str, object]:
    return {
        "suite_name": suite.name,
        "suite_tags": list(suite.tags),
        "scenario_count": len(scenarios),
        "scenarios": [_evaluation_scenario_definition_body(item) for item in scenarios],
    }


def _evaluation_scenario_definition_body(scenario: EvaluationScenario) -> dict[str, object]:
    return {
        "scenario_name": scenario.name,
        "kind": scenario.kind.value,
        "tags": list(scenario.tags),
        "goal": {
            "description": scenario.goal.description,
            "success_criteria": [item.key for item in scenario.goal.success_criteria],
        },
        "task": {
            "description": scenario.task.description,
            "required_criteria": list(scenario.task.required_criteria),
        },
    }


def _evaluation_report_body(recording: EvaluationReportRecording) -> dict[str, object]:
    return {
        "suite_name": recording.suite_name,
        "passed": recording.passed,
        "summary": _evaluation_summary_body(recording.summary),
        "scenarios": [_evaluation_scenario_body(item) for item in recording.scenarios],
    }


def _evaluation_summary_body(summary: EvaluationSummaryRecording) -> dict[str, object]:
    return {
        "scenario_count": summary.scenario_count,
        "passed_count": summary.passed_count,
        "failed_count": summary.failed_count,
        "goal_completed_count": summary.goal_completed_count,
        "task_completed_count": summary.task_completed_count,
        "action_started_count": summary.action_started_count,
        "action_completed_count": summary.action_completed_count,
        "tool_failure_count": summary.tool_failure_count,
        "policy_denial_count": summary.policy_denial_count,
        "recovery_planned_count": summary.recovery_planned_count,
        "human_intervention_count": summary.human_intervention_count,
        "resource_conflict_count": summary.resource_conflict_count,
        "active_resource_lock_count": summary.active_resource_lock_count,
        "execution_duration_ms": summary.execution_duration_ms,
        "model_call_count": summary.model_call_count,
        "model_total_token_count": summary.model_total_token_count,
        "model_estimated_cost_micros": summary.model_estimated_cost_micros,
    }


def _evaluation_scenario_body(scenario: EvaluationScenarioRecording) -> dict[str, object]:
    return {
        "scenario_name": scenario.scenario_name,
        "kind": scenario.kind.value,
        "tags": list(scenario.tags),
        "passed": scenario.passed,
        "result_status": scenario.result_status.value,
        "error_code": None if scenario.error_code is None else scenario.error_code.value,
        "satisfied_criteria": dict(scenario.satisfied_criteria),
        "checks": [_evaluation_check_body(check) for check in scenario.checks],
        "event_types": list(scenario.event_types),
        "action_capabilities": list(scenario.action_capabilities),
        "audit_capabilities": list(scenario.audit_capabilities),
        "evidence_claims": list(scenario.evidence_claims),
    }


def _evaluation_gate_body(gate: EvaluationGateRecording) -> dict[str, object]:
    return {
        "passed": gate.passed,
        "checks": [_evaluation_check_body(check) for check in gate.checks],
    }


def _evaluation_check_body(check: EvaluationCheckRecording) -> dict[str, object]:
    return {"name": check.name, "passed": check.passed, "message": check.message}


def _replay_report_body(report: ReplayReport) -> dict[str, object]:
    return {
        "scenario_name": report.actual.scenario_name,
        "passed": report.passed,
        "checks": [_replay_check_body(check) for check in report.checks],
        "failed_checks": [_replay_check_body(check) for check in report.failed_checks],
        "expected": encode_replay_recording(report.expected),
        "actual": encode_replay_recording(report.actual),
    }


def _replay_recordings_body(
    recording_dir: str,
    recordings: tuple[ReplayRecording, ...],
) -> dict[str, object]:
    return {
        "recording_dir": recording_dir,
        "recording_count": len(recordings),
        "recordings": [_replay_recording_summary_body(item) for item in recordings],
    }


def _replay_recording_summary_body(recording: ReplayRecording) -> dict[str, object]:
    return {
        "scenario_name": recording.scenario_name,
        "result_status": recording.result_status.value,
        "error_code": None if recording.error_code is None else recording.error_code.value,
        "event_count": recording.metrics.event_count,
        "action_started_count": recording.metrics.action_started_count,
        "policy_denial_count": recording.metrics.policy_denial_count,
        "recovery_planned_count": recording.metrics.recovery_planned_count,
        "resource_conflict_count": recording.metrics.resource_conflict_count,
        "model_total_token_count": recording.metrics.model_total_token_count,
        "model_estimated_cost_micros": recording.metrics.model_estimated_cost_micros,
        "action_capabilities": list(recording.action_capabilities),
        "policy_effects": list(recording.policy_effects),
        "audit_capabilities": [item.capability for item in recording.audit_entries],
    }


def _replay_check_body(check: ReplayCheck) -> dict[str, object]:
    return {"name": check.name, "passed": check.passed, "message": check.message}


def _evaluation_comparison_body(comparison: EvaluationReportComparison) -> dict[str, object]:
    return {
        "passed": comparison.passed,
        "checks": [_comparison_check_body(check) for check in comparison.checks],
        "failed_checks": [_comparison_check_body(check) for check in comparison.failed_checks],
    }


def _comparison_check_body(check: EvaluationReportComparisonCheck) -> dict[str, object]:
    return {
        "name": check.name,
        "passed": check.passed,
        "message": check.message,
    }


def _write_json(out: TextIO, payload: object) -> None:
    json.dump(_json_safe(payload), out, indent=2, sort_keys=True)
    out.write("\n")


def _write_text(out: TextIO, payload: str) -> None:
    out.write(payload)


def _write_error(out: TextIO, code: str, message: str) -> None:
    _write_json(out, {"error": {"code": code, "message": message}})


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
