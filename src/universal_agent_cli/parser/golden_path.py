"""Golden Path command parsers: init, run, session, config, profile, doctor."""

from __future__ import annotations

import argparse

from universal_agent_cli.contributions import CliDomainContribution
from universal_agent_cli.defaults import (
    default_distributed_locks_path,
    default_store_path,
    default_work_queue_path,
    default_workers_path,
)

from ._helpers import add_output_argument


def add_init_parser(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    contributions: tuple[CliDomainContribution, ...] = (),
) -> None:
    init = commands.add_parser(
        "init",
        usage=(
            "%(prog)s [--output PATH] [--global] [--profile NAME] "
            "[--model-provider PROVIDER] [--model-name NAME] "
            "[--model-api-key-env ENV] [--force] [advanced options]"
        ),
        description=(
            "First-time setup. Creates ./universal-agent/profile.json and "
            "./universal-agent/config.json by default; use --global for ~/.universal-agent/."
        ),
        help="Create the local profile config (first-time setup; idempotent).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    first_day = init.add_argument_group("First-day options")
    advanced_runtime = init.add_argument_group("Advanced: runtime/store options")
    advanced_distributed = init.add_argument_group("Advanced: distributed runtime options")
    # Domain backends are contributed: the shell owns the group and the
    # generic --domain-backend selector; domains add their own options.
    advanced_domain = init.add_argument_group("Advanced: domain backend options")
    advanced_model = init.add_argument_group("Advanced: model transport options")
    first_day.add_argument(
        "--output",
        default=None,
        help="Profile config file to write (default: universal-agent/profile.json in cwd).",
    )
    first_day.add_argument(
        "--global",
        dest="global_config",
        action="store_true",
        help=(
            "Write user-level config to ~/.universal-agent/profile.json instead of "
            "./universal-agent/."
        ),
    )
    first_day.add_argument(
        "--output-format",
        choices=("text", "json"),
        default="text",
        help="Human summary (text, default) or machine JSON.",
    )
    first_day.add_argument(
        "--profile",
        default="default",
        help="Profile name to generate (default: default).",
    )
    advanced_runtime.add_argument("--environment", default="local")
    advanced_runtime.add_argument(
        "--store-backend",
        choices=("memory", "file", "sqlite"),
        default="file",
    )
    advanced_runtime.add_argument("--store-path", default=default_store_path())
    advanced_distributed.add_argument(
        "--distributed-queue-backend",
        choices=("memory", "file", "sqlite"),
        default="memory",
    )
    advanced_distributed.add_argument("--distributed-queue-path", default=default_work_queue_path())
    advanced_distributed.add_argument(
        "--distributed-locks-backend", choices=("memory", "file", "sqlite"), default="memory"
    )
    advanced_distributed.add_argument(
        "--distributed-locks-path", default=default_distributed_locks_path()
    )
    advanced_distributed.add_argument(
        "--distributed-workers-backend",
        choices=("memory", "file", "sqlite"),
        default="memory",
    )
    advanced_distributed.add_argument("--distributed-workers-path", default=default_workers_path())
    advanced_distributed.add_argument("--distributed-terminal-retention-seconds", type=float)
    domain_backends = ("fake", *(b for c in contributions for b in c.init_backends))
    advanced_domain.add_argument(
        "--domain-backend",
        choices=domain_backends,
        default="fake",
    )
    for contribution in contributions:
        if contribution.init_add_arguments is not None:
            contribution.init_add_arguments(advanced_domain)
    first_day.add_argument(
        "--model-provider",
        choices=("scripted", "json_http", "openai_chat_completions", "openai_responses"),
        default="scripted",
    )
    first_day.add_argument(
        "--model-provider-preset",
        choices=("360zhinao", "deepseek", "moonshot"),
        help="Apply provider/model/response-format/timeout defaults for a known provider.",
    )
    first_day.add_argument("--model-name", default="scripted")
    first_day.add_argument("--model-api-key-env")
    first_day.add_argument(
        "--server-url",
        help=(
            "Remote agentd Runtime URL for thin-client mode (client/server "
            "separation); stored in config.json `server.url`"
        ),
    )
    first_day.add_argument(
        "--server-auth-token-env",
        help=(
            "Environment variable holding the agentd bearer token (name only; "
            "the value stays out of the config file)"
        ),
    )
    advanced_model.add_argument("--model-endpoint")
    advanced_model.add_argument("--model-api-key-file")
    advanced_model.add_argument("--model-api-key-secret", default="model_api_key")
    advanced_model.add_argument(
        "--model-timeout-seconds",
        type=float,
        # 120s default: reasoning models with json_object response formats
        # routinely exceed 30s per call (UA-LIVE-2026-09-21 F5).
        default=120.0,
    )
    advanced_model.add_argument(
        "--model-response-format",
        choices=("json_schema", "json_object", "prompt_json"),
        help=(
            "Response format for openai_chat_completions profiles. "
            "Use prompt_json for legacy-compatible providers without response_format support."
        ),
    )
    advanced_model.add_argument("--model-header", action="append", default=[])
    first_day.add_argument("--force", action="store_true")


def add_run_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    run = commands.add_parser(
        "run",
        help="Run one Agent goal and print the session summary (creates a Session).",
    )
    run.add_argument(
        "profile",
        nargs="?",
        default=None,
        help="Optional profile name positional (prefer --profile).",
    )
    run.add_argument("goal", help="Goal description text for the Agent.")
    run.add_argument(
        "--profile",
        dest="profile_option",
        default=None,
        help="Profile to run (default: the service's primary profile).",
    )
    run.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="Human-readable summary (text, default) or machine JSON.",
    )
    run.add_argument("--task")
    run.add_argument(
        "--compile-goal",
        action="store_true",
        help="Compile the goal description into the initial runtime task graph.",
    )
    run.add_argument(
        "--timeout-seconds",
        type=float,
        help="Pause at the next clean runtime boundary after this wall-clock budget expires.",
    )
    run.add_argument(
        "--success",
        action="append",
        default=[],
        help="Goal success criterion as KEY=JSON. Repeat for multiple criteria.",
    )
    run.add_argument(
        "--allow-unverified-mutation",
        action="store_true",
        help=(
            "Proceed with a mutation-shaped goal without explicit --success "
            "criteria (the default healthy criterion can be satisfied "
            "pre-mutation; P10b)."
        ),
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run in read-only mode: the agent investigates but mutation "
            "capabilities are unavailable (spec P1 section 13)."
        ),
    )


def add_session_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    session = commands.add_parser(
        "session",
        help="List, inspect, resume or cancel persisted Agent sessions.",
    )
    session_commands = session.add_subparsers(dest="session_command", required=True)

    list_sessions = session_commands.add_parser(
        "list",
        help="List recent sessions (text by default; --output json for machines).",
    )
    list_sessions.add_argument("--after")
    list_sessions.add_argument("--limit", type=int)
    list_sessions.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="Human table (text, default) or machine JSON.",
    )

    show = session_commands.add_parser(
        "show",
        help="Show one session: status, goal, event timeline, evidence and action counts.",
    )
    show.add_argument("session_id")
    show.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="Human report (text, default) or machine JSON.",
    )

    explain = session_commands.add_parser(
        "explain",
        help="Explain a failed or waiting session with Error / Reason / Try guidance.",
    )
    explain.add_argument("session_id")
    explain.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="Human guidance (text, default) or machine JSON.",
    )

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

    pause = session_commands.add_parser("pause", help="Move a running session into waiting.")
    pause.add_argument("session_id")
    pause.add_argument("--reason", default="session paused from CLI")
    add_output_argument(pause)

    resume = session_commands.add_parser(
        "resume", help="Resume a waiting/paused session (--confirmed true approves an action)."
    )
    resume.add_argument("session_id")
    resume.add_argument("--confirmed", choices=("true", "false"))
    add_output_argument(resume)

    cancel = session_commands.add_parser("cancel", help="Cancel a non-terminal session.")
    cancel.add_argument("session_id")
    cancel.add_argument("--reason", default="session cancelled from CLI")
    add_output_argument(cancel)


def add_config_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    config = commands.add_parser(
        "config",
        help="Show the active configuration (model, profile, runtime, secrets, domains).",
    )
    config_commands = config.add_subparsers(dest="config_command", required=False)
    config_show = config_commands.add_parser(
        "show",
        help="Print the full machine-readable config JSON (advanced).",
    )
    config_show.add_argument(
        "--output",
        choices=("text", "json"),
        default="json",
        help="Output format for `config show` (json default; text for humans).",
    )
    config_validate = config_commands.add_parser("validate")
    config_validate.add_argument(
        "--skip-secret-resolution",
        action="store_true",
        help="Validate config shape without checking env/file secret availability.",
    )


def add_profile_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    profile = commands.add_parser(
        "profile",
        help="List available profiles or show one profile's model/domains/policy.",
    )
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_list = profile_commands.add_parser(
        "list",
        help="List profiles (text by default; --output json for machines).",
    )
    profile_list.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="Name list (text, default) or machine JSON.",
    )
    profile_show = profile_commands.add_parser(
        "show",
        help="Show one profile: model, domains, policy, capabilities.",
    )
    profile_show.add_argument("profile")
    profile_show.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="Human report (text, default) or machine JSON.",
    )
    profile_verify = profile_commands.add_parser("verify")
    profile_verify.add_argument("--profile-dir", required=True)
    profile_add_domain = profile_commands.add_parser(
        "add-domain",
        help="Add a secondary domain to an existing profile config.",
    )
    from universal_agent_cli.profile_domains import add_add_domain_arguments

    add_add_domain_arguments(profile_add_domain)
    profile_create = profile_commands.add_parser(
        "create",
        help="Create a stored profile from a profile JSON file (agentd profile store).",
    )
    profile_create.add_argument(
        "--from",
        dest="from_file",
        required=True,
        help="Path to the profile JSON payload to store.",
    )
    profile_delete = profile_commands.add_parser(
        "delete",
        help="Delete a stored profile from the agentd profile store.",
    )
    profile_delete.add_argument("profile")


def add_doctor_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    doctor = commands.add_parser(
        "doctor",
        help=(
            "Check environment, config, model, runtime, profiles, domains and policy; print fixes."
        ),
    )
    doctor.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="Human-readable report (text, default) or machine JSON.",
    )
    doctor.add_argument(
        "--fail-on",
        choices=("never", "error", "warn"),
        default="error",
        help="Exit with status 1 when Doctor status reaches the selected severity.",
    )
