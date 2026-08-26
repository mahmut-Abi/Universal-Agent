from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TextIO, cast

from universal_agent.agentd.app import (
    AgentdApp,
    AgentdAuthPolicy,
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
    domain_package_body,
    evaluator_body,
    event_batch_body,
    health_body,
    log_records_body,
    memory_body,
    metrics_body,
    multi_agent_body,
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
    state_event_repair_body,
    tool_body,
    trace_spans_body,
)
from universal_agent.agentd.server import AgentdHttpServer, AgentdServerConfig
from universal_agent.core import (
    ActionId,
    Decision,
    DecisionType,
    DomainIdentity,
    ErrorCode,
    EventId,
    ExecutionStatus,
    Goal,
    JsonMapping,
    JsonValue,
    SessionId,
    SuccessCriterion,
    Task,
    TaskId,
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
from universal_agent.domain import (
    DomainLoader,
    DomainPackage,
    DomainPackageCompatibility,
    DomainPackageNotFoundError,
    DomainPackageRuntimeActivation,
    DomainPackageScaffoldResult,
    DomainPackageScaffoldSpec,
    DomainPackageVerificationReport,
    RuntimeBuilder,
    load_domain_package_runtime,
    scaffold_domain_package,
)
from universal_agent.domains.kubernetes import (
    KubectlBackend,
    KubernetesApiBackend,
    KubernetesBackend,
    KubernetesMutationBackend,
    KubernetesRemediationDomain,
)
from universal_agent.ecosystem import (
    EcosystemCatalog,
    EcosystemCatalogVerificationReport,
    EcosystemInstallPlan,
    EcosystemInstallResult,
    EcosystemRegistryIndex,
    EcosystemRegistryManifest,
    EcosystemRegistryNotFoundError,
    EcosystemRegistryStoreNotFoundError,
    EcosystemRegistryTrustPolicy,
    FileEcosystemRegistryStore,
    encode_ecosystem_registry_manifest,
    install_ecosystem,
    load_ecosystem_catalog,
    load_ecosystem_registry_index,
    load_ecosystem_registry_manifest,
    plan_ecosystem_install,
    write_ecosystem_registry_manifest,
)
from universal_agent.evaluation.console import (
    build_evaluation_console_snapshot,
    render_evaluation_console,
    render_evaluation_console_text,
)
from universal_agent.evaluation.dataset import (
    EvaluationDataset,
    EvaluationDatasetIdentity,
    EvaluationDatasetNotFoundError,
    EvaluationDatasetRegistry,
    EvaluationDatasetVerificationReport,
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
from universal_agent.host import (
    DomainConfig,
    RuntimeConfig,
    RuntimeHost,
    build_configured_model_adapter,
)
from universal_agent.model import ScriptedModelAdapter
from universal_agent.profile import (
    AgentProfile,
    ProfileCatalogEntry,
    ProfileCatalogVerificationReport,
    ProfileConfig,
    ProfileConfigNotFoundError,
    load_profile_catalog,
)
from universal_agent.runtime import AgentRuntime, InMemoryEventSink, RuntimeAPI, RuntimeEventBatch
from universal_agent.security import EnvSecretProvider, SecretProvider, resolve_secret_value
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
    secret_provider = EnvSecretProvider()
    backend = _configured_kubernetes_backend(
        profile.runtime.configured_domains() or (profile.domain,),
        config=profile.runtime,
        secret_provider=secret_provider,
    )
    host = RuntimeHost.from_profile(
        profile=profile,
        model=build_configured_model_adapter(
            profile.runtime,
            scripted_decisions=_default_decisions(),
            secret_provider=secret_provider,
        ),
        domain=KubernetesRemediationDomain(
            cast(KubernetesBackend, backend),
            cast(KubernetesMutationBackend, backend),
        ),
        secret_provider=secret_provider,
    )
    return host.service


def _configured_kubernetes_backend(
    domains: tuple[DomainConfig, ...],
    *,
    config: RuntimeConfig | None = None,
    secret_provider: SecretProvider | None = None,
) -> object:
    if not domains:
        return _DefaultCliBackend()
    primary = domains[0]
    backend = primary.backend or "fake"
    if backend == "fake":
        return _DefaultCliBackend()
    if backend == "kubectl":
        return KubectlBackend(
            default_namespace=_setting_string(
                primary.settings,
                "default_namespace",
                default="default",
            ),
            context=_optional_setting_string(primary.settings, "context"),
            kubeconfig=_optional_setting_string(primary.settings, "kubeconfig"),
            timeout_seconds=_setting_float(primary.settings, "timeout_seconds", default=10.0),
        )
    if backend == "kubernetes_api":
        return KubernetesApiBackend(
            api_server=_setting_string(primary.settings, "api_server", default=""),
            bearer_token=_configured_kubernetes_api_token(
                primary.settings,
                config=config,
                secret_provider=secret_provider,
            ),
            default_namespace=_setting_string(
                primary.settings,
                "default_namespace",
                default="default",
            ),
            timeout_seconds=_setting_float(primary.settings, "timeout_seconds", default=10.0),
        )
    raise ValueError(f"unsupported Kubernetes domain backend: {backend}")


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
    commands.add_parser("multi-agent")
    repair = commands.add_parser("repair")
    repair_commands = repair.add_subparsers(dest="repair_command", required=True)
    repair_state_events = repair_commands.add_parser("state-events")
    repair_state_events.add_argument("--confirmed", choices=("true", "false"), default="false")
    repair_state_events.add_argument("--dry-run", action="store_true")

    distributed = commands.add_parser("distributed")
    distributed_commands = distributed.add_subparsers(
        dest="distributed_command",
        required=True,
    )
    distributed_commands.add_parser("snapshot")
    distributed_commands.add_parser("health")
    distributed_commands.add_parser("expire")
    distributed_prune = distributed_commands.add_parser("prune-terminal")
    distributed_prune.add_argument("--before")
    distributed_schedule = distributed_commands.add_parser("schedule-session")
    distributed_schedule.add_argument("session_id")
    distributed_schedule.add_argument("--priority", type=int, default=0)
    distributed_schedule.add_argument("--max-attempts", type=int, default=3)
    distributed_schedule_goal = distributed_commands.add_parser("schedule-goal")
    distributed_schedule_goal.add_argument("profile")
    distributed_schedule_goal.add_argument("goal")
    distributed_schedule_goal.add_argument("--task", default="Run goal")
    distributed_schedule_goal.add_argument(
        "--success",
        action="append",
        default=[],
        help="Goal success criterion as KEY=JSON. Repeat for multiple criteria.",
    )
    distributed_schedule_goal.add_argument("--priority", type=int, default=0)
    distributed_schedule_goal.add_argument("--max-attempts", type=int, default=3)
    distributed_schedule_task = distributed_commands.add_parser("schedule-task")
    distributed_schedule_task.add_argument("session_id")
    distributed_schedule_task.add_argument("task_id")
    distributed_schedule_task.add_argument("--priority", type=int, default=0)
    distributed_schedule_task.add_argument("--max-attempts", type=int, default=3)
    distributed_schedule_action = distributed_commands.add_parser("schedule-action")
    distributed_schedule_action.add_argument("session_id")
    distributed_schedule_action.add_argument("task_id")
    distributed_schedule_action.add_argument("action_id")
    distributed_schedule_action.add_argument(
        "--confirmed", choices=("true", "false"), required=True
    )
    distributed_schedule_action.add_argument("--priority", type=int, default=0)
    distributed_schedule_action.add_argument("--max-attempts", type=int, default=3)
    distributed_pending_actions = distributed_commands.add_parser("schedule-pending-actions")
    distributed_pending_actions.add_argument(
        "--confirmed", choices=("true", "false"), required=True
    )
    distributed_pending_actions.add_argument("--priority", type=int, default=0)
    distributed_pending_actions.add_argument("--max-attempts", type=int, default=3)
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

    distributed_worker_run = distributed_commands.add_parser("worker-run-once")
    distributed_worker_run.add_argument("worker_id")
    distributed_worker_run.add_argument("--lease-ttl-seconds", type=float, default=30.0)
    distributed_worker_run.add_argument("--worker-ttl-seconds", type=float, default=30.0)
    distributed_worker_run.add_argument("--heartbeat-interval-seconds", type=float)
    distributed_worker_run_batch = distributed_commands.add_parser("worker-run")
    distributed_worker_run_batch.add_argument("worker_id")
    distributed_worker_run_batch.add_argument("--max-items", type=int, default=1)
    distributed_worker_run_batch.add_argument("--lease-ttl-seconds", type=float, default=30.0)
    distributed_worker_run_batch.add_argument("--worker-ttl-seconds", type=float, default=30.0)
    distributed_worker_run_batch.add_argument("--heartbeat-interval-seconds", type=float)

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
    init.add_argument(
        "--distributed-queue-backend",
        choices=("memory", "file", "sqlite"),
        default="memory",
    )
    init.add_argument("--distributed-queue-path", default=".universal-agent/work-queue.json")
    init.add_argument(
        "--distributed-locks-backend", choices=("memory", "file", "sqlite"), default="memory"
    )
    init.add_argument("--distributed-locks-path", default=".universal-agent/distributed-locks.json")
    init.add_argument(
        "--distributed-workers-backend",
        choices=("memory", "file", "sqlite"),
        default="memory",
    )
    init.add_argument("--distributed-workers-path", default=".universal-agent/workers.json")
    init.add_argument("--distributed-terminal-retention-seconds", type=float)
    init.add_argument(
        "--domain-backend",
        choices=("fake", "kubectl", "kubernetes_api"),
        default="fake",
    )
    init.add_argument("--kubectl-namespace", default="default")
    init.add_argument("--kubectl-context")
    init.add_argument("--kubectl-kubeconfig")
    init.add_argument("--kubectl-timeout-seconds", type=float, default=10.0)
    init.add_argument("--kubernetes-api-server")
    init.add_argument("--kubernetes-api-namespace", default="default")
    init.add_argument("--kubernetes-api-token-env")
    init.add_argument("--kubernetes-api-token-file")
    init.add_argument("--kubernetes-api-token-secret", default="kubernetes_api_token")
    init.add_argument("--kubernetes-api-timeout-seconds", type=float, default=10.0)
    init.add_argument(
        "--model-provider",
        choices=("scripted", "json_http", "openai_responses"),
        default="scripted",
    )
    init.add_argument("--model-name", default="scripted")
    init.add_argument("--model-endpoint")
    init.add_argument("--model-api-key-env")
    init.add_argument("--model-api-key-file")
    init.add_argument("--model-api-key-secret", default="model_api_key")
    init.add_argument("--model-timeout-seconds", type=float, default=30.0)
    init.add_argument("--model-header", action="append", default=[])
    init.add_argument("--force", action="store_true")

    config = commands.add_parser("config")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("show")

    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--auth-token")
    serve.add_argument("--auth-token-env")
    serve.add_argument("--read-only-auth-token")
    serve.add_argument("--read-only-auth-token-env")
    serve.add_argument("--evaluation-report-dir")

    run = commands.add_parser("run")
    run.add_argument("profile")
    run.add_argument("goal")
    run.add_argument("--task", default="Run goal")
    run.add_argument(
        "--success",
        action="append",
        default=[],
        help="Goal success criterion as KEY=JSON. Repeat for multiple criteria.",
    )

    tui = commands.add_parser("tui")
    tui.add_argument("--session-id")
    tui.add_argument("--session-limit", type=int, default=5)
    tui.add_argument("--event-limit", type=int, default=12)

    ecosystem = commands.add_parser("ecosystem")
    ecosystem_commands = ecosystem.add_subparsers(dest="ecosystem_command", required=True)
    ecosystem_catalog = ecosystem_commands.add_parser("catalog")
    ecosystem_catalog.add_argument("--domain-package-dir")
    ecosystem_catalog.add_argument("--dataset-dir")
    ecosystem_catalog.add_argument("--profile-dir")
    ecosystem_verify = ecosystem_commands.add_parser("verify")
    ecosystem_verify.add_argument("--domain-package-dir")
    ecosystem_verify.add_argument("--dataset-dir")
    ecosystem_verify.add_argument("--profile-dir")
    ecosystem_export = ecosystem_commands.add_parser("export")
    ecosystem_export.add_argument("--domain-package-dir")
    ecosystem_export.add_argument("--dataset-dir")
    ecosystem_export.add_argument("--profile-dir")
    ecosystem_export.add_argument("--name", default="local-ecosystem")
    ecosystem_export.add_argument("--version", default="0.1.0")
    ecosystem_export.add_argument(
        "--description",
        default="Local Universal Agent ecosystem registry",
    )
    ecosystem_export.add_argument("--output")
    ecosystem_export.add_argument("--force", action="store_true")
    ecosystem_registry = ecosystem_commands.add_parser("registry")
    ecosystem_registry.add_argument("manifest")
    ecosystem_registry.add_argument("--verify", action="store_true")
    ecosystem_install = ecosystem_commands.add_parser("install")
    ecosystem_install.add_argument("manifest")
    ecosystem_install.add_argument("--base-path")
    ecosystem_install.add_argument("--no-verify", action="store_true")
    ecosystem_install.add_argument("--plan-only", action="store_true")
    ecosystem_install.add_argument("--allow-unverified-signatures", action="store_true")
    ecosystem_store = ecosystem_commands.add_parser("store")
    ecosystem_store_commands = ecosystem_store.add_subparsers(
        dest="ecosystem_store_command",
        required=True,
    )
    ecosystem_store_save = ecosystem_store_commands.add_parser("save")
    ecosystem_store_save.add_argument("manifest")
    ecosystem_store_save.add_argument("--store-dir", required=True)
    ecosystem_store_save.add_argument("--force", action="store_true")
    ecosystem_store_list = ecosystem_store_commands.add_parser("list")
    ecosystem_store_list.add_argument("--store-dir", required=True)
    ecosystem_store_show = ecosystem_store_commands.add_parser("show")
    ecosystem_store_show.add_argument("name")
    ecosystem_store_show.add_argument("version")
    ecosystem_store_show.add_argument("--store-dir", required=True)
    ecosystem_store_show.add_argument("--verify", action="store_true")

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
    eval_console.add_argument("--format", choices=("html", "text"), default="html")

    eval_datasets = eval_commands.add_parser("datasets")
    eval_datasets.add_argument("--dataset-dir", required=True)
    eval_datasets.add_argument("--tag")
    eval_datasets.add_argument("--domain")
    eval_datasets.add_argument("--verify", action="store_true")

    eval_dataset = eval_commands.add_parser("dataset")
    eval_dataset.add_argument("name")
    eval_dataset.add_argument("version", nargs="?")
    eval_dataset.add_argument("--dataset-dir", required=True)

    domain = commands.add_parser("domain")
    domain_commands = domain.add_subparsers(dest="domain_command", required=True)
    domain_commands.add_parser("list")

    domain_packages = commands.add_parser("domain-packages")
    domain_package_commands = domain_packages.add_subparsers(
        dest="domain_packages_command",
        required=True,
    )
    domain_package_list = domain_package_commands.add_parser("list")
    domain_package_list.add_argument("--tag")
    domain_package_show = domain_package_commands.add_parser("show")
    domain_package_show.add_argument("name")
    domain_package_show.add_argument("version", nargs="?")
    domain_package_verify = domain_package_commands.add_parser("verify")
    domain_package_verify.add_argument("--local-paths", action="store_true")
    domain_package_load_runtime = domain_package_commands.add_parser("load-runtime")
    domain_package_load_runtime.add_argument("path")
    domain_package_load_runtime.add_argument("--skip-local-paths", action="store_true")
    domain_package_scaffold = domain_package_commands.add_parser("scaffold")
    domain_package_scaffold.add_argument("name")
    domain_package_scaffold.add_argument("--description", required=True)
    domain_package_scaffold.add_argument("--output", required=True)
    domain_package_scaffold.add_argument("--version", default="0.1.0")
    domain_package_scaffold.add_argument("--api-version", default="agent.nantian.dev/v1alpha1")
    domain_package_scaffold.add_argument("--author")
    domain_package_scaffold.add_argument("--entrypoint")
    domain_package_scaffold.add_argument("--ontology", action="append", default=[])
    domain_package_scaffold.add_argument("--capability", action="append", default=[])
    domain_package_scaffold.add_argument("--tool", action="append", default=[])
    domain_package_scaffold.add_argument("--policy", action="append", default=[])
    domain_package_scaffold.add_argument("--procedure", action="append", default=[])
    domain_package_scaffold.add_argument("--knowledge", action="append", default=[])
    domain_package_scaffold.add_argument("--evaluator", action="append", default=[])
    domain_package_scaffold.add_argument("--context-provider", action="append", default=[])
    domain_package_scaffold.add_argument("--prompt", action="append", default=[])
    domain_package_scaffold.add_argument("--resource", action="append", default=[])
    domain_package_scaffold.add_argument("--dependency", action="append", default=[])
    domain_package_scaffold.add_argument("--required-tool", action="append", default=[])
    domain_package_scaffold.add_argument("--runtime-api")
    domain_package_scaffold.add_argument("--domain-api")
    domain_package_scaffold.add_argument(
        "--side-effects",
        choices=("none", "reversible", "destructive"),
        default="none",
    )
    domain_package_scaffold.add_argument("--requires-confirmation", action="store_true")
    domain_package_scaffold.add_argument("--tag", action="append", default=[])
    domain_package_scaffold.add_argument("--runtime-stub", action="store_true")
    domain_package_scaffold.add_argument("--force", action="store_true")

    profile = commands.add_parser("profile")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_commands.add_parser("list")
    profile_show = profile_commands.add_parser("show")
    profile_show.add_argument("profile")
    profile_verify = profile_commands.add_parser("verify")
    profile_verify.add_argument("--profile-dir", required=True)

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
    world.add_argument("--entity")
    world.add_argument("--relation")

    events = session_commands.add_parser("events")
    events.add_argument("session_id")
    events.add_argument("--after")
    events.add_argument("--limit", type=int)
    events.add_argument("--format", choices=("json", "sse"), default="json")
    events.add_argument("--wait", action="store_true")
    events.add_argument("--timeout-seconds", type=float, default=10.0)
    events.add_argument("--poll-interval-seconds", type=float, default=0.25)

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
        _dispatch_serve(args, service, out, server_runner=server_runner)
        return
    if command == "run":
        await _dispatch_run(args, service, out)
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


def _dispatch_ecosystem(args: argparse.Namespace, out: TextIO) -> None:
    command = cast(str, args.ecosystem_command)
    if command == "catalog":
        catalog = _load_ecosystem_catalog_from_args(args)
        _write_json(out, _ecosystem_catalog_body(catalog))
        return
    if command == "verify":
        catalog = _load_ecosystem_catalog_from_args(args)
        _write_json(out, _ecosystem_verification_body(catalog))
        return
    if command == "export":
        catalog = _load_ecosystem_catalog_from_args(args)
        manifest = catalog.registry_manifest(
            name=cast(str, args.name),
            version=cast(str, args.version),
            description=cast(str, args.description),
        )
        output = cast(str | None, args.output)
        if output is None:
            _write_json(out, encode_ecosystem_registry_manifest(manifest))
            return
        write_result = write_ecosystem_registry_manifest(
            output,
            manifest,
            overwrite=cast(bool, args.force),
        )
        _write_json(
            out,
            {
                "status": "updated" if write_result.overwritten else "created",
                "path": str(write_result.path),
                "manifest": encode_ecosystem_registry_manifest(write_result.manifest),
            },
        )
        return
    if command == "registry":
        index = load_ecosystem_registry_index(cast(str, args.manifest))
        if cast(bool, args.verify):
            _write_json(out, _ecosystem_verification_report_body(index.verify()))
            return
        _write_json(out, encode_ecosystem_registry_manifest(index.manifest))
        return
    if command == "install":
        index = load_ecosystem_registry_index(cast(str, args.manifest))
        base_path = cast(str | None, args.base_path)
        verify = not cast(bool, args.no_verify)
        trust_policy = EcosystemRegistryTrustPolicy(
            allow_unverified_signatures=cast(bool, args.allow_unverified_signatures)
        )
        if cast(bool, args.plan_only):
            plan = plan_ecosystem_install(
                index,
                base_path=base_path,
                verify=verify,
                trust_policy=trust_policy,
            )
            _write_json(out, _ecosystem_install_plan_body(plan))
            return
        install_result = install_ecosystem(
            index,
            base_path=base_path,
            verify=verify,
            trust_policy=trust_policy,
        )
        _write_json(out, _ecosystem_install_result_body(install_result))
        return
    if command == "store":
        _dispatch_ecosystem_store(args, out)
        return
    raise ValueError(f"unknown ecosystem command: {command}")


def _dispatch_ecosystem_store(args: argparse.Namespace, out: TextIO) -> None:
    store = FileEcosystemRegistryStore(cast(str, args.store_dir))
    command = cast(str, args.ecosystem_store_command)
    if command == "save":
        manifest = load_ecosystem_registry_manifest(cast(str, args.manifest))
        write_result = store.save(manifest, overwrite=cast(bool, args.force))
        _write_json(
            out,
            {
                "status": "updated" if write_result.overwritten else "created",
                "path": str(write_result.path),
                "manifest": _ecosystem_registry_summary_body(write_result.manifest),
            },
        )
        return
    if command == "list":
        manifests = store.list_manifests()
        _write_json(
            out,
            {
                "registry_count": len(manifests),
                "registries": [_ecosystem_registry_summary_body(item) for item in manifests],
            },
        )
        return
    if command == "show":
        manifest = store.load(cast(str, args.name), cast(str, args.version))
        if cast(bool, args.verify):
            _write_json(
                out, _ecosystem_verification_report_body(EcosystemRegistryIndex(manifest).verify())
            )
            return
        _write_json(out, encode_ecosystem_registry_manifest(manifest))
        return
    raise ValueError(f"unknown ecosystem store command: {command}")


def _load_ecosystem_catalog_from_args(args: argparse.Namespace) -> EcosystemCatalog:
    return load_ecosystem_catalog(
        domain_package_root=cast(str | None, args.domain_package_dir),
        evaluation_dataset_root=cast(str | None, args.dataset_dir),
        profile_root=cast(str | None, args.profile_dir),
    )


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
        snapshot = build_evaluation_console_snapshot(report_dir)
        if cast(str, args.format) == "text":
            _write_text(out, render_evaluation_console_text(snapshot))
            return
        _write_text(out, render_evaluation_console(snapshot))
        return
    if command == "datasets":
        registry = _evaluation_dataset_registry(args)
        if cast(bool, args.verify):
            _write_json(out, evaluation_dataset_verification_body(registry.verify()))
            return
        domain = cast(str | None, args.domain)
        _write_json(
            out,
            {
                "datasets": [
                    _evaluation_dataset_body(dataset)
                    for dataset in registry.list(
                        tag=cast(str | None, args.tag),
                        domain=None if domain is None else _parse_domain_identity(domain),
                    )
                ]
            },
        )
        return
    if command == "dataset":
        registry = _evaluation_dataset_registry(args)
        version = cast(str | None, args.version)
        dataset = (
            registry.get_by_name(cast(str, args.name))
            if version is None
            else registry.get(EvaluationDatasetIdentity(cast(str, args.name), version))
        )
        _write_json(out, _evaluation_dataset_body(dataset))
        return
    raise ValueError(f"unknown eval command: {command}")


def _evaluation_dataset_registry(args: argparse.Namespace) -> EvaluationDatasetRegistry:
    registry = EvaluationDatasetRegistry()
    registry.discover(Path(cast(str, args.dataset_dir)))
    return registry


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
    if command == "verify":
        catalog = load_profile_catalog(cast(str, args.profile_dir))
        _write_json(out, profile_catalog_verification_body(catalog.verify()))
        return
    raise ValueError(f"unknown profile command: {command}")


def _dispatch_domain_packages(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    command = cast(str, args.domain_packages_command)
    if command == "list":
        tag = cast(str | None, args.tag)
        _write_json(
            out,
            {
                "domain_packages": [
                    domain_package_body(item) for item in service.domain_packages(tag=tag)
                ]
            },
        )
        return
    if command == "show":
        _write_json(
            out,
            domain_package_body(
                service.domain_package(cast(str, args.name), cast(str | None, args.version))
            ),
        )
        return
    if command == "verify":
        _write_json(
            out,
            domain_package_verification_body(
                service.domain_package_verification(
                    verify_paths=cast(bool, args.local_paths),
                )
            ),
        )
        return
    if command == "load-runtime":
        activation = load_domain_package_runtime(
            Path(cast(str, args.path)),
            verify_paths=not cast(bool, args.skip_local_paths),
        )
        _write_json(out, domain_package_runtime_activation_body(activation))
        return
    if command == "scaffold":
        result = scaffold_domain_package(
            Path(cast(str, args.output)),
            _domain_package_scaffold_spec(args),
            overwrite=cast(bool, args.force),
        )
        _write_json(out, domain_package_scaffold_body(result))
        return
    raise ValueError(f"unknown domain package command: {command}")


def _domain_package_scaffold_spec(args: argparse.Namespace) -> DomainPackageScaffoldSpec:
    return DomainPackageScaffoldSpec(
        name=cast(str, args.name),
        version=cast(str, args.version),
        description=cast(str, args.description),
        api_version=cast(str, args.api_version),
        author=cast(str | None, args.author),
        entrypoint=cast(str | None, args.entrypoint),
        ontology=tuple(cast(list[str], args.ontology)),
        capabilities=tuple(cast(list[str], args.capability)),
        tools=tuple(cast(list[str], args.tool)),
        policies=tuple(cast(list[str], args.policy)),
        procedures=tuple(cast(list[str], args.procedure)),
        knowledge=tuple(cast(list[str], args.knowledge)),
        evaluators=tuple(cast(list[str], args.evaluator)),
        context_providers=tuple(cast(list[str], args.context_provider)),
        prompts=tuple(cast(list[str], args.prompt)),
        resources=tuple(cast(list[str], args.resource)),
        dependencies=tuple(
            _parse_domain_identity(item) for item in cast(list[str], args.dependency)
        ),
        required_tools=tuple(cast(list[str], args.required_tool)),
        compatibility=DomainPackageCompatibility(
            runtime_api=cast(str | None, args.runtime_api),
            domain_api=cast(str | None, args.domain_api),
        ),
        security=immutable_json(
            {
                "side_effects": cast(str, args.side_effects),
                "requires_confirmation": cast(bool, args.requires_confirmation),
            }
        ),
        tags=tuple(cast(list[str], args.tag)),
        runtime_stub=cast(bool, args.runtime_stub),
    )


def _parse_domain_identity(value: str) -> DomainIdentity:
    if "@" not in value:
        raise ValueError(f"domain package dependency must be name@version: {value}")
    name, version = value.split("@", 1)
    if not name.strip() or not version.strip():
        raise ValueError(f"domain package dependency must be name@version: {value}")
    return DomainIdentity(name, version)


def domain_package_scaffold_body(result: DomainPackageScaffoldResult) -> dict[str, object]:
    package = result.package
    return {
        "status": "updated" if result.overwritten else "created",
        "name": package.identity.name,
        "version": package.identity.version,
        "root_path": str(package.root_path),
        "manifest_path": str(package.manifest_path),
        "created_paths": [str(path) for path in result.created_paths],
        "written_paths": [str(path) for path in result.written_paths],
        "runtime_stub_paths": [str(path) for path in result.runtime_stub_paths],
    }


def domain_package_runtime_activation_body(
    activation: DomainPackageRuntimeActivation,
) -> dict[str, object]:
    package = activation.package
    active = activation.active_domain
    return {
        "status": "loaded",
        "metadata_verified": True,
        "package": {
            "name": package.identity.name,
            "version": package.identity.version,
            "entrypoint": package.manifest.entrypoint,
            "root_path": str(package.root_path),
            "manifest_path": str(package.manifest_path),
        },
        "active_domain": {
            "name": active.identity.name,
            "version": active.identity.version,
            "description": active.manifest.metadata.description,
            "capability_names": [capability.name for capability in active.capabilities],
            "tool_names": [tool.definition.name for tool in active.tools],
            "policy_names": [policy.name for policy in active.policies],
            "evaluator_names": [evaluator.name for evaluator in active.evaluators],
            "context_provider_names": [provider.name for provider in active.context_providers],
            "evidence_extractor_count": len(active.evidence_extractors),
            "world_updater_count": len(active.world_updaters),
            "task_expander_count": len(active.task_expanders),
            "recovery_rule_count": len(active.recovery_rules),
            "memory_count": len(active.memories),
        },
    }


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
        distributed_queue_backend=cast(str, args.distributed_queue_backend),
        distributed_queue_path=cast(str, args.distributed_queue_path),
        distributed_locks_backend=cast(str, args.distributed_locks_backend),
        distributed_locks_path=cast(str, args.distributed_locks_path),
        distributed_workers_backend=cast(str, args.distributed_workers_backend),
        distributed_workers_path=cast(str, args.distributed_workers_path),
        distributed_terminal_retention_seconds=cast(
            float | None, args.distributed_terminal_retention_seconds
        ),
        domain_backend=cast(str, args.domain_backend),
        kubectl_namespace=cast(str, args.kubectl_namespace),
        kubectl_context=cast(str | None, args.kubectl_context),
        kubectl_kubeconfig=cast(str | None, args.kubectl_kubeconfig),
        kubectl_timeout_seconds=cast(float, args.kubectl_timeout_seconds),
        kubernetes_api_server=cast(str | None, args.kubernetes_api_server),
        kubernetes_api_namespace=cast(str, args.kubernetes_api_namespace),
        kubernetes_api_token_env=cast(str | None, args.kubernetes_api_token_env),
        kubernetes_api_token_file=cast(str | None, args.kubernetes_api_token_file),
        kubernetes_api_token_secret=cast(str, args.kubernetes_api_token_secret),
        kubernetes_api_timeout_seconds=cast(float, args.kubernetes_api_timeout_seconds),
        model_provider=cast(str, args.model_provider),
        model_name=cast(str, args.model_name),
        model_endpoint=cast(str | None, args.model_endpoint),
        model_api_key_env=cast(str | None, args.model_api_key_env),
        model_api_key_file=cast(str | None, args.model_api_key_file),
        model_api_key_secret=cast(str, args.model_api_key_secret),
        model_timeout_seconds=cast(float, args.model_timeout_seconds),
        model_headers=_parse_key_value_options(cast(list[str], args.model_header), "model-header"),
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
    distributed_queue_backend: str,
    distributed_queue_path: str,
    distributed_locks_backend: str,
    distributed_locks_path: str,
    distributed_workers_backend: str,
    distributed_workers_path: str,
    distributed_terminal_retention_seconds: float | None,
    domain_backend: str,
    kubectl_namespace: str,
    kubectl_context: str | None,
    kubectl_kubeconfig: str | None,
    kubectl_timeout_seconds: float,
    kubernetes_api_server: str | None,
    kubernetes_api_namespace: str,
    kubernetes_api_token_env: str | None,
    kubernetes_api_token_file: str | None,
    kubernetes_api_token_secret: str,
    kubernetes_api_timeout_seconds: float,
    model_provider: str,
    model_name: str,
    model_endpoint: str | None,
    model_api_key_env: str | None,
    model_api_key_file: str | None,
    model_api_key_secret: str,
    model_timeout_seconds: float,
    model_headers: dict[str, str],
) -> dict[str, object]:
    model_secret_source = _single_secret_source(
        "--model-api-key",
        env_key=model_api_key_env,
        file_path=model_api_key_file,
    )
    kubernetes_api_token_source = _single_secret_source(
        "--kubernetes-api-token",
        env_key=kubernetes_api_token_env,
        file_path=kubernetes_api_token_file,
    )
    domain = _profile_domain_config(
        domain_backend=domain_backend,
        kubectl_namespace=kubectl_namespace,
        kubectl_context=kubectl_context,
        kubectl_kubeconfig=kubectl_kubeconfig,
        kubectl_timeout_seconds=kubectl_timeout_seconds,
        kubernetes_api_server=kubernetes_api_server,
        kubernetes_api_namespace=kubernetes_api_namespace,
        kubernetes_api_token_secret=(
            kubernetes_api_token_secret if kubernetes_api_token_source is not None else None
        ),
        kubernetes_api_timeout_seconds=kubernetes_api_timeout_seconds,
    )
    store: dict[str, str] = {"backend": store_backend}
    if store_backend != "memory":
        store["path"] = store_path
    distributed_queue: dict[str, str] = {"backend": distributed_queue_backend}
    if distributed_queue_backend != "memory":
        distributed_queue["path"] = distributed_queue_path
    distributed_locks: dict[str, str] = {"backend": distributed_locks_backend}
    if distributed_locks_backend != "memory":
        distributed_locks["path"] = distributed_locks_path
    distributed_workers: dict[str, str] = {"backend": distributed_workers_backend}
    if distributed_workers_backend != "memory":
        distributed_workers["path"] = distributed_workers_path
    runtime: dict[str, object] = {
        "environment": {"environment": environment},
        "model": _profile_model_config(
            model_provider=model_provider,
            model_name=model_name,
            model_endpoint=model_endpoint,
            model_api_key_source=model_secret_source,
            model_api_key_secret=model_api_key_secret,
            model_timeout_seconds=model_timeout_seconds,
            model_headers=model_headers,
        ),
        "store": store,
        "distributed_queue": distributed_queue,
        "distributed_locks": distributed_locks,
        "distributed_workers": distributed_workers,
        "limits": {"max_iterations": 20, "max_recovery_steps": 8},
        "domain": domain,
    }
    secrets: dict[str, dict[str, object]] = {}
    if model_secret_source is not None:
        _add_secret(secrets, model_api_key_secret, model_secret_source)
    if kubernetes_api_token_source is not None:
        _add_secret(secrets, kubernetes_api_token_secret, kubernetes_api_token_source)
    if secrets:
        runtime["secrets"] = secrets
    if distributed_terminal_retention_seconds is not None:
        runtime["distributed_terminal_retention_seconds"] = distributed_terminal_retention_seconds
    return {
        "name": profile_name,
        "version": "0.1.0",
        "description": "Local Kubernetes profile",
        "domain": domain,
        "runtime": runtime,
    }


def _profile_domain_config(
    *,
    domain_backend: str,
    kubectl_namespace: str,
    kubectl_context: str | None,
    kubectl_kubeconfig: str | None,
    kubectl_timeout_seconds: float,
    kubernetes_api_server: str | None,
    kubernetes_api_namespace: str,
    kubernetes_api_token_secret: str | None,
    kubernetes_api_timeout_seconds: float,
) -> dict[str, object]:
    domain: dict[str, object] = {"name": "kubernetes", "version": "0.2.0"}
    if domain_backend == "fake":
        return domain
    if domain_backend == "kubectl":
        settings: dict[str, object] = {
            "default_namespace": kubectl_namespace,
            "timeout_seconds": kubectl_timeout_seconds,
        }
        if kubectl_context:
            settings["context"] = kubectl_context
        if kubectl_kubeconfig:
            settings["kubeconfig"] = kubectl_kubeconfig
        domain["backend"] = "kubectl"
        domain["settings"] = settings
        return domain
    if domain_backend == "kubernetes_api":
        if kubernetes_api_server is None or not kubernetes_api_server.strip():
            raise ValueError("kubernetes_api backend requires --kubernetes-api-server")
        settings = {
            "api_server": kubernetes_api_server,
            "default_namespace": kubernetes_api_namespace,
            "timeout_seconds": kubernetes_api_timeout_seconds,
        }
        if kubernetes_api_token_secret is not None:
            settings["bearer_token_secret"] = kubernetes_api_token_secret
        domain["backend"] = "kubernetes_api"
        domain["settings"] = settings
        return domain
    raise ValueError(f"unsupported domain backend: {domain_backend}")


def _single_secret_source(
    label: str,
    *,
    env_key: str | None,
    file_path: str | None,
) -> tuple[str, str] | None:
    if env_key is not None and file_path is not None:
        raise ValueError(f"{label} accepts either env or file, not both")
    if env_key is not None:
        return ("env", env_key)
    if file_path is not None:
        return ("file", file_path)
    return None


def _add_secret(
    secrets: dict[str, dict[str, object]],
    name: str,
    source: tuple[str, str],
) -> None:
    source_name, key = source
    if not name.strip():
        raise ValueError("secret name must not be empty")
    if not key.strip():
        raise ValueError(f"secret {name} {source_name} key must not be empty")
    if name in secrets:
        raise ValueError(f"duplicate runtime secret: {name}")
    secrets[name] = {"source": source_name, "key": key, "required": True}


def _profile_model_config(
    *,
    model_provider: str,
    model_name: str,
    model_endpoint: str | None,
    model_api_key_source: tuple[str, str] | None,
    model_api_key_secret: str,
    model_timeout_seconds: float,
    model_headers: dict[str, str],
) -> dict[str, object]:
    model: dict[str, object] = {
        "provider": model_provider,
        "name": model_name,
        "timeout_seconds": model_timeout_seconds,
    }
    if model_provider == "scripted":
        if model_endpoint is not None:
            raise ValueError("scripted model does not accept --model-endpoint")
        if model_api_key_source is not None:
            raise ValueError("scripted model does not accept model API key secrets")
        if model_headers:
            raise ValueError("scripted model does not accept --model-header")
        return model
    if model_provider == "json_http":
        if model_endpoint is None or not model_endpoint.strip():
            raise ValueError("json_http model requires --model-endpoint")
        model["endpoint"] = model_endpoint
    elif model_provider == "openai_responses":
        if model_api_key_source is None:
            raise ValueError("openai_responses model requires model API key secret")
        if model_endpoint is not None:
            if not model_endpoint.strip():
                raise ValueError("openai_responses model endpoint must not be empty")
            model["endpoint"] = model_endpoint
    else:
        raise ValueError(f"unsupported model provider: {model_provider}")
    if model_api_key_source is not None:
        model["api_key_secret"] = model_api_key_secret
    if model_headers:
        model["headers"] = model_headers
    return model


def _parse_key_value_options(values: Sequence[str], label: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        key, separator, option_value = value.partition("=")
        if not separator or not key.strip() or not option_value.strip():
            raise ValueError(f"{label} must be KEY=VALUE")
        if key in parsed:
            raise ValueError(f"duplicate {label}: {key}")
        parsed[key] = option_value
    return parsed


def _success_criteria(values: Sequence[str]) -> tuple[SuccessCriterion, ...]:
    if not values:
        return (SuccessCriterion("healthy", True),)
    parsed: dict[str, JsonValue] = {}
    for value in values:
        key, separator, raw_expected = value.partition("=")
        if not separator or not key.strip() or not raw_expected.strip():
            raise ValueError("success criterion must be KEY=JSON")
        key = key.strip()
        if key in parsed:
            raise ValueError(f"duplicate success criterion: {key}")
        parsed[key] = _parse_success_json_value(raw_expected, key)
    return tuple(SuccessCriterion(key, expected) for key, expected in parsed.items())


def _parse_success_json_value(value: str, key: str) -> JsonValue:
    try:
        loaded: object = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"success criterion {key} must be valid JSON") from exc
    return _json_value(loaded, f"success.{key}")


def _json_value(value: object, field: str) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list):
        return [_json_value(item, f"{field}[]") for item in value]
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field} keys must be strings")
            result[key] = _json_value(item, f"{field}.{key}")
        return result
    raise ValueError(f"{field} must be JSON-compatible")


def _setting_string(settings: JsonMapping, key: str, *, default: str) -> str:
    value = settings.get(key, default)
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError(f"domain setting {key} must be a non-empty string")


def _optional_setting_string(settings: JsonMapping, key: str) -> str | None:
    value = settings.get(key)
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError(f"domain setting {key} must be a non-empty string")


def _configured_kubernetes_api_token(
    settings: JsonMapping,
    *,
    config: RuntimeConfig | None,
    secret_provider: SecretProvider | None,
) -> str | None:
    secret_name = _optional_setting_string(settings, "bearer_token_secret")
    if secret_name is None:
        return None
    if config is None:
        raise ValueError("kubernetes_api bearer_token_secret requires runtime config")
    for secret in config.secrets:
        if secret.name == secret_name:
            return resolve_secret_value(secret, provider=secret_provider)
    raise ValueError(f"domain setting bearer_token_secret is not declared: {secret_name}")


def _setting_float(settings: JsonMapping, key: str, *, default: float) -> float:
    value: JsonValue = settings.get(key, default)
    if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
        return float(value)
    raise ValueError(f"domain setting {key} must be a positive number")


def _dispatch_serve(
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
        (server_runner or _serve_forever)(server)
    finally:
        server.server_close()


def _serve_forever(server: AgentdHttpServer) -> None:
    server.serve_forever()


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
    if timeout_seconds < 0.0 or timeout_seconds > 30.0:
        raise ValueError("timeout_seconds must be between 0 and 30")
    if poll_interval_seconds < 0.001 or poll_interval_seconds > 5.0:
        raise ValueError("poll_interval_seconds must be between 0.001 and 5")

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


def _ecosystem_catalog_body(catalog: EcosystemCatalog) -> dict[str, object]:
    summary = catalog.summary
    return {
        "summary": {
            "domain_package_count": summary.domain_package_count,
            "evaluation_dataset_count": summary.evaluation_dataset_count,
            "profile_count": summary.profile_count,
            "total_items": summary.total_items,
        },
        "domain_packages": [
            _ecosystem_domain_package_body(package) for package in catalog.domain_packages
        ],
        "evaluation_datasets": [
            _evaluation_dataset_body(dataset) for dataset in catalog.evaluation_datasets
        ],
        "profiles": [_ecosystem_profile_body(entry) for entry in catalog.profiles],
    }


def _ecosystem_verification_body(catalog: EcosystemCatalog) -> dict[str, object]:
    return _ecosystem_verification_report_body(catalog.verify())


def _ecosystem_verification_report_body(
    report: EcosystemCatalogVerificationReport,
) -> dict[str, object]:
    return {
        "passed": report.passed,
        "failed_check_count": len(report.failed_checks),
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "message": check.message,
            }
            for check in report.checks
        ],
    }


def _ecosystem_registry_summary_body(manifest: EcosystemRegistryManifest) -> dict[str, object]:
    return {
        "name": manifest.name,
        "version": manifest.version,
        "description": manifest.description,
        "summary": {
            "domain_package_count": manifest.summary.domain_package_count,
            "evaluation_dataset_count": manifest.summary.evaluation_dataset_count,
            "profile_count": manifest.summary.profile_count,
            "total_items": manifest.summary.total_items,
        },
    }


def _ecosystem_install_plan_body(plan: EcosystemInstallPlan) -> dict[str, object]:
    return {
        "status": "planned",
        "domain_package_count": len(plan.domain_packages.candidates),
        "evaluation_dataset_count": len(plan.evaluation_datasets),
        "profile_count": len(plan.profiles),
        "domain_packages": [
            _ecosystem_domain_package_body(candidate.package)
            for candidate in plan.domain_packages.candidates
        ],
        "evaluation_datasets": [
            _evaluation_dataset_body(candidate.dataset) for candidate in plan.evaluation_datasets
        ],
        "profiles": [_ecosystem_profile_body(candidate.entry) for candidate in plan.profiles],
    }


def _ecosystem_install_result_body(result: EcosystemInstallResult) -> dict[str, object]:
    domain_package_registry_count = len(result.domain_packages.identities())
    return {
        "status": "installed",
        "domain_package_count": len(result.installed_domain_packages),
        "evaluation_dataset_count": len(result.installed_evaluation_datasets),
        "profile_count": len(result.installed_profiles),
        "registry_count": domain_package_registry_count,
        "domain_package_registry_count": domain_package_registry_count,
        "evaluation_dataset_registry_count": len(result.evaluation_datasets.identities()),
        "profile_registry_count": len(result.profiles.all()),
        "domain_packages": [
            _ecosystem_domain_package_body(package) for package in result.installed_domain_packages
        ],
        "evaluation_datasets": [
            _evaluation_dataset_body(dataset) for dataset in result.installed_evaluation_datasets
        ],
        "profiles": [_ecosystem_profile_body(entry) for entry in result.installed_profiles],
    }


def _ecosystem_domain_package_body(package: DomainPackage) -> dict[str, object]:
    manifest = package.manifest
    return {
        "name": package.identity.name,
        "version": package.identity.version,
        "description": manifest.description,
        "author": manifest.author,
        "entrypoint": manifest.entrypoint,
        "tags": list(manifest.tags),
        "ontology": list(manifest.ontology),
        "capability_names": list(manifest.capabilities),
        "tool_names": list(manifest.tools),
        "policy_names": list(manifest.policies),
        "procedure_names": list(manifest.procedures),
        "knowledge_names": list(manifest.knowledge),
        "evaluator_names": list(manifest.evaluators),
        "context_provider_names": list(manifest.context_providers),
        "prompt_names": list(manifest.prompts),
        "resource_names": list(manifest.resources),
        "dependencies": [
            {"name": dependency.name, "version": dependency.version}
            for dependency in manifest.dependencies
        ],
        "required_tools": list(manifest.required_tools),
        "compatibility": {
            "runtime_api": manifest.compatibility.runtime_api,
            "domain_api": manifest.compatibility.domain_api,
        },
        "security": dict(manifest.security),
        "root_path": str(package.root_path),
        "manifest_path": str(package.manifest_path),
    }


def domain_package_verification_body(
    report: DomainPackageVerificationReport,
) -> dict[str, object]:
    return {
        "passed": report.passed,
        "failed_check_count": len(report.failed_checks),
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "message": check.message,
            }
            for check in report.checks
        ],
    }


def evaluation_dataset_verification_body(
    report: EvaluationDatasetVerificationReport,
) -> dict[str, object]:
    return {
        "passed": report.passed,
        "failed_check_count": len(report.failed_checks),
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "message": check.message,
            }
            for check in report.checks
        ],
    }


def profile_catalog_verification_body(
    report: ProfileCatalogVerificationReport,
) -> dict[str, object]:
    return {
        "passed": report.passed,
        "failed_check_count": len(report.failed_checks),
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "message": check.message,
            }
            for check in report.checks
        ],
    }


def _ecosystem_profile_body(entry: ProfileCatalogEntry) -> dict[str, object]:
    profile = entry.profile
    return {
        "name": profile.name,
        "version": profile.version,
        "description": profile.description,
        "domains": [
            {"name": domain.name, "version": domain.version}
            for domain in profile.configured_domains()
        ],
        "path": str(entry.path),
    }


def _evaluation_dataset_body(dataset: EvaluationDataset) -> dict[str, object]:
    return {
        "name": dataset.identity.name,
        "version": dataset.identity.version,
        "description": dataset.manifest.description,
        "author": dataset.manifest.author,
        "tags": list(dataset.manifest.tags),
        "domains": [
            {"name": domain.name, "version": domain.version} for domain in dataset.manifest.domains
        ],
        "suite_count": len(dataset.manifest.suites),
        "suites": [
            {
                "name": suite.name,
                "path": suite.path,
                "description": suite.description,
                "tags": list(suite.tags),
                "suite_path": str(dataset.suite_path(suite)),
            }
            for suite in dataset.manifest.suites
        ],
        "root_path": str(dataset.root_path),
        "manifest_path": str(dataset.manifest_path),
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


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("--before must be an ISO 8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError("--before must include a timezone")
    return parsed


def _package_version() -> str:
    try:
        return version("universal-agent-runtime")
    except PackageNotFoundError:
        return "0.1.0"


if __name__ == "__main__":
    raise SystemExit(main())
