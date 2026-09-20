"""Advanced/developer command parsers (non-Golden-Path surface)."""

from __future__ import annotations

import argparse


def add_observability_parsers(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    commands.add_parser("version")
    commands.add_parser("health")
    commands.add_parser("ready")
    metrics = commands.add_parser("metrics")
    metrics.add_argument("--format", choices=("json", "prometheus"), default="json")
    commands.add_parser("cost")
    commands.add_parser("logs")
    traces = commands.add_parser("traces")
    traces.add_argument("--format", choices=("runtime", "otlp"), default="runtime")
    audit = commands.add_parser("audit")
    audit.add_argument("--integrity", action="store_true")
    commands.add_parser(
        "multi-agent",
        help="(advanced/experimental) Optional Multi-Agent registry and delegation surface.",
    )
    repair = commands.add_parser("repair")
    repair_commands = repair.add_subparsers(dest="repair_command", required=True)
    repair_state_events = repair_commands.add_parser("state-events")
    repair_state_events.add_argument("--confirmed", choices=("true", "false"), default="false")
    repair_state_events.add_argument("--dry-run", action="store_true")


def add_serve_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--auth-token")
    serve.add_argument("--auth-token-env")
    serve.add_argument("--read-only-auth-token")
    serve.add_argument("--read-only-auth-token-env")
    serve.add_argument("--evaluation-report-dir")


def add_tui_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    tui = commands.add_parser("tui")
    tui.add_argument("--session-id")
    tui.add_argument("--session-limit", type=int, default=5)
    tui.add_argument("--event-limit", type=int, default=12)
    tui.add_argument(
        "--static",
        action="store_true",
        help="Render a deterministic one-shot snapshot instead of the interactive dashboard",
    )


def add_ecosystem_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    ecosystem = commands.add_parser(
        "ecosystem",
        help="(advanced/experimental) Inspect, verify or plan local ecosystem packages.",
    )
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
    ecosystem_install.add_argument("--allow-unsigned", action="store_true")
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


def add_catalog_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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


def add_chat_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    # Lazy import: parser/__init__ imports this module during its own init.

    chat = commands.add_parser("chat", help="(advanced) Interactive conversation with the runtime")
    # Default None → the dispatch resolves the service's primary profile, so
    # chat works against any runtime regardless of the user config file.
    chat.add_argument("--profile", default=None)
    chat.add_argument(
        "--show-events",
        action="store_true",
        help="Print runtime events after each turn",
    )


def add_admin_parser(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """(advanced) User / tenant / role / credential management plane.

    Talks to the agentd admin API (``/v1/admin/*``); the server must have an
    admin credential store configured. Denials surface as CLI errors.
    """

    admin = commands.add_parser(
        "admin",
        help="(advanced) Manage users, tenants, roles and credentials via agentd.",
    )
    admin_sub = admin.add_subparsers(dest="admin_command", required=True)

    tenant = admin_sub.add_parser("tenant")
    tenant_sub = tenant.add_subparsers(dest="admin_verb", required=True)
    tenant_create = tenant_sub.add_parser("create")
    tenant_create.add_argument("--tenant-id", required=True)
    tenant_create.add_argument("--name", required=True)
    tenant_sub.add_parser("list")
    tenant_status = tenant_sub.add_parser("disable")
    tenant_status.add_argument("--tenant", required=True)
    tenant_enable = tenant_sub.add_parser("enable")
    tenant_enable.add_argument("--tenant", required=True)

    user = admin_sub.add_parser("user")
    user_sub = user.add_subparsers(dest="admin_verb", required=True)
    user_create = user_sub.add_parser("create")
    user_create.add_argument("--user-id", required=True)
    user_create.add_argument("--email", required=True)
    user_create.add_argument("--display-name")
    user_sub.add_parser("list")
    user_disable = user_sub.add_parser("disable")
    user_disable.add_argument("--user", required=True)
    user_enable = user_sub.add_parser("enable")
    user_enable.add_argument("--user", required=True)

    role = admin_sub.add_parser("role")
    role_sub = role.add_subparsers(dest="admin_verb", required=True)
    role_set = role_sub.add_parser("set")
    role_set.add_argument("--tenant", required=True)
    role_set.add_argument("--user", required=True)
    role_set.add_argument(
        "--role",
        required=True,
        choices=("admin", "operator", "read_only"),
    )

    member = admin_sub.add_parser("member")
    member_sub = member.add_subparsers(dest="admin_verb", required=True)
    member_list = member_sub.add_parser("list")
    member_list.add_argument("--tenant", required=True)
    member_remove = member_sub.add_parser("remove")
    member_remove.add_argument("--tenant", required=True)
    member_remove.add_argument("--user", required=True)

    credential = admin_sub.add_parser("credential")
    credential_sub = credential.add_subparsers(dest="admin_verb", required=True)
    credential_create = credential_sub.add_parser("create")
    credential_create.add_argument("--user", required=True)
    credential_create.add_argument("--tenant", required=True)
    credential_create.add_argument(
        "--role",
        required=True,
        choices=("admin", "operator", "read_only"),
    )
    credential_revoke = credential_sub.add_parser("revoke")
    credential_revoke.add_argument("--credential-id", required=True)
    credential_list = credential_sub.add_parser("list")
    credential_list.add_argument("--user")
    credential_list.add_argument("--tenant")


def add_memory_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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
