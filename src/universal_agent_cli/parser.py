from __future__ import annotations

import argparse

from universal_agent.domains.kubernetes.cli import LOCAL_PROFILE_NAME, add_kubernetes_command
from universal_agent.evaluation.harness import EvaluationScenarioKind
from universal_agent_cli.defaults import (
    default_distributed_locks_path,
    default_init_output_path,
    default_store_path,
    default_work_queue_path,
    default_workers_path,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent")
    parser.add_argument(
        "--profile-config",
        help="Load an Agent Profile JSON config before dispatching the command.",
    )
    parser.add_argument(
        "--api-url",
        help="Forward supported commands to a running agentd Runtime API.",
    )
    parser.add_argument("--api-token", help="Bearer token for --api-url requests.")
    parser.add_argument(
        "--api-token-env",
        help="Environment variable containing the bearer token for --api-url requests.",
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
    doctor = commands.add_parser("doctor")
    doctor.add_argument(
        "--fail-on",
        choices=("never", "error", "warn"),
        default="never",
        help="Exit with status 1 when Doctor status reaches the selected severity.",
    )
    audit = commands.add_parser("audit")
    audit.add_argument("--integrity", action="store_true")
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
    init.add_argument("--output", default=default_init_output_path())
    init.add_argument("--profile", default=LOCAL_PROFILE_NAME)
    init.add_argument("--environment", default="local")
    init.add_argument("--store-backend", choices=("memory", "file", "sqlite"), default="file")
    init.add_argument("--store-path", default=default_store_path())
    init.add_argument(
        "--distributed-queue-backend",
        choices=("memory", "file", "sqlite"),
        default="memory",
    )
    init.add_argument("--distributed-queue-path", default=default_work_queue_path())
    init.add_argument(
        "--distributed-locks-backend", choices=("memory", "file", "sqlite"), default="memory"
    )
    init.add_argument("--distributed-locks-path", default=default_distributed_locks_path())
    init.add_argument(
        "--distributed-workers-backend",
        choices=("memory", "file", "sqlite"),
        default="memory",
    )
    init.add_argument("--distributed-workers-path", default=default_workers_path())
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
        choices=("scripted", "json_http", "openai_chat_completions", "openai_responses"),
        default="scripted",
    )
    init.add_argument("--model-name", default="scripted")
    init.add_argument("--model-endpoint")
    init.add_argument("--model-api-key-env")
    init.add_argument("--model-api-key-file")
    init.add_argument("--model-api-key-secret", default="model_api_key")
    init.add_argument("--model-timeout-seconds", type=float, default=30.0)
    init.add_argument(
        "--model-response-format",
        choices=("json_schema", "json_object", "prompt_json"),
        help=(
            "Response format for openai_chat_completions profiles. "
            "Use prompt_json for legacy-compatible providers without response_format support."
        ),
    )
    init.add_argument("--model-header", action="append", default=[])
    init.add_argument("--force", action="store_true")

    config = commands.add_parser("config")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("show")
    config_validate = config_commands.add_parser("validate")
    config_validate.add_argument(
        "--skip-secret-resolution",
        action="store_true",
        help="Validate config shape without checking env/file secret availability.",
    )

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
    run.add_argument("--task")
    run.add_argument(
        "--compile-goal",
        action="store_true",
        help="Compile the goal description into the initial runtime task graph.",
    )
    run.add_argument(
        "--success",
        action="append",
        default=[],
        help="Goal success criterion as KEY=JSON. Repeat for multiple criteria.",
    )

    add_kubernetes_command(commands)

    tui = commands.add_parser("tui")
    tui.add_argument("--session-id")
    tui.add_argument("--session-limit", type=int, default=5)
    tui.add_argument("--event-limit", type=int, default=12)
    tui.add_argument(
        "--static",
        action="store_true",
        help="Render a deterministic one-shot snapshot instead of the interactive dashboard",
    )

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

    chat = commands.add_parser("chat", help="Interactive conversation with the runtime")
    chat.add_argument("--profile", default=LOCAL_PROFILE_NAME)
    chat.add_argument(
        "--show-events",
        action="store_true",
        help="Print runtime events after each turn",
    )

    memory = commands.add_parser("memory")
    memory_sub = memory.add_subparsers(dest="memory_command", required=False)
    memory_add = memory_sub.add_parser("add", help="Create a memory record")
    memory_add.add_argument(
        "--kind",
        default="semantic",
        choices=("semantic", "episodic", "procedural", "preference"),
    )
    memory_add.add_argument("--subject", required=True)
    memory_add.add_argument("--content", required=True)
    memory_add.add_argument("--scope", default="")
    memory_add.add_argument("--confidence", type=float, default=1.0)
    memory_get = memory_sub.add_parser("get", help="Fetch a memory record by id")
    memory_get.add_argument("memory_id")
    memory_delete = memory_sub.add_parser("delete", help="Delete a memory record by id")
    memory_delete.add_argument("memory_id")
    memory_sub.add_parser("list", help="List memory records")

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
    audit.add_argument("--integrity", action="store_true")

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


def _add_evaluation_selector_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--kind",
        action="append",
        choices=tuple(item.value for item in EvaluationScenarioKind),
    )
    command.add_argument("--tag", action="append")
    command.add_argument("--exclude-tag", action="append")
