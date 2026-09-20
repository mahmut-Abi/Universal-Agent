"""Module entry point for the agentd HTTP server.

Allows the client packages to launch an embedded runtime as a subprocess
(process-isolated HTTP communication) without importing kernel internals:

    python -m universal_agent.agentd --port 0 --port-file /tmp/agentd.port

The service is built from the same profile-config machinery the CLI uses.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from pathlib import Path

from universal_agent.agentd.app import AgentdApp
from universal_agent.agentd.http import AgentdAuthPolicy
from universal_agent.agentd.server import AgentdHttpServer, AgentdServerConfig
from universal_agent.core.config_validation import parse_non_empty_string
from universal_agent.policy import Policy
from universal_agent.profile.store import ProfileStore
from universal_agent.security import AuditRecorder, CredentialAdminStore, EnvSecretProvider
from universal_agent.service import RuntimeService


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m universal_agent.agentd",
        description="Serve the Universal Agent Runtime API over HTTP.",
    )
    parser.add_argument("--profile-config")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument(
        "--port-file",
        help="Write the bound port to this file so a launcher can discover it.",
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="Build the service without requiring the configured model to connect "
        "(for probe-style commands that never execute model calls).",
    )
    parser.add_argument("--auth-token")
    parser.add_argument("--auth-token-env")
    parser.add_argument("--read-only-auth-token")
    parser.add_argument("--read-only-auth-token-env")
    parser.add_argument("--evaluation-report-dir")
    parser.add_argument(
        "--admin-store",
        choices=("memory", "postgres"),
        help=(
            "Enable the admin plane (/v1/admin/*) backed by a credential store. "
            "memory: in-process (dev/test only, not durable). postgres: durable "
            "schema v3 principal tables; requires --admin-store-url-env. When "
            "set, bearer tokens resolve to principals and RBAC applies."
        ),
    )
    parser.add_argument(
        "--admin-store-url-env",
        help=(
            "Name of the environment variable holding the Postgres DSN "
            "for --admin-store postgres."
        ),
    )
    parser.add_argument(
        "--audit-log",
        help=(
            "Append-only JSONL audit sink for security events "
            "(default: in-memory when the admin plane is on)."
        ),
    )
    parser.add_argument(
        "--tenant-id",
        help=(
            "Tenant this server's store is scoped to; requests resolving to a "
            "different tenant are refused (cross-tenant denial)."
        ),
    )
    parser.add_argument(
        "--deployment-config",
        help="Path to the deployment config JSON (default: $AGENT_CONFIG_DIR/deployment.json)",
    )
    parser.add_argument(
        "--profiles-dir",
        help=(
            "Directory for persisted profile configs managed through the "
            "config-management write API (default: $AGENT_CONFIG_DIR/profiles "
            "or ./universal-agent/profiles)"
        ),
    )
    parser.add_argument(
        "--default-domain",
        default="local",
        help=(
            "Default service domain when no profile config is supplied. Names "
            "resolve through the universal_agent.default_domains entry-point "
            "group (registered domain default services)."
        ),
    )
    return parser


def _build_service_from_profile(
    profile_config: str,
    *,
    extra_policies: tuple[Policy, ...] = (),
) -> RuntimeService:
    """Build a RuntimeService from a profile config file.

    Delegates to the shared domains-package composition point so agentd uses
    the same profile dispatch as the CLI and the SDK facade: profiles with
    domain_package_paths load their packaged domains; local profiles use the
    domain-neutral local service; everything else uses the matching built-in
    domain profile service.
    """

    from universal_agent.domains.profile_service import build_configured_service
    from universal_agent.host import RuntimeHost, build_configured_model_adapter
    from universal_agent.profile import ProfileConfig
    from universal_agent.security import EnvSecretProvider

    profile = ProfileConfig.from_json_file(profile_config).to_profile()
    secret_provider = EnvSecretProvider()
    if profile.runtime.domain_package_paths:
        return RuntimeHost.from_configured_domain_packages(
            config=profile.runtime,
            model=build_configured_model_adapter(profile.runtime, secret_provider=secret_provider),
            profile=profile,
            secret_provider=secret_provider,
            extra_policies=extra_policies,
        ).service
    # For non-domain-package profiles, use the profile_service dispatch and
    # apply config policies at the RuntimeHost level.
    service = build_configured_service(profile_config)
    return _with_extra_policies(service, extra_policies)


def _profile_service_factory(
    profile_store: ProfileStore | None,
    extra_policies: tuple[Policy, ...],
) -> Callable[[str], RuntimeService] | None:
    """Lazy per-profile service builder for the hot-swap registry.

    Mirrors the CLI serve factory: persisted profile configs are built through
    the shared composition point and wrapped with the deployment's extra
    policies. Returns None when no profile store is configured, which leaves
    hot-swap disabled (the app answers with a structured bad_request).
    """

    if profile_store is None:
        return None

    def factory(profile_name: str) -> RuntimeService:
        from universal_agent.profile import ProfileConfigNotFoundError

        config_path = profile_store.config_path(profile_name)
        if not config_path.is_file():
            raise ProfileConfigNotFoundError(f"profile config not found: {profile_name}")
        service = _build_service_from_profile(str(config_path), extra_policies=extra_policies)
        return _with_extra_policies(service, extra_policies)

    return factory


def _build_default_with_policies(
    domain_name: str,
    extra_policies: tuple[Policy, ...],
) -> RuntimeService:
    """Build the default service and thread config-declared policies."""

    from universal_agent.domains.profile_service import build_default_domain_service

    service = build_default_domain_service(domain_name)
    return _with_extra_policies(service, extra_policies)


def _with_extra_policies(
    service: RuntimeService,
    extra_policies: tuple[Policy, ...],
) -> RuntimeService:
    """Rebuild the service's policy engine with config-declared policies.

    Uses RuntimeService's runtime_api.runtime.components to access and replace
    the PolicyEngine; no domain-specific code is involved.
    """

    if not extra_policies:
        return service
    from dataclasses import replace as dc_replace

    from universal_agent.policy import PolicyEngine

    runtime = service._runtime_api._runtime
    old_engine = runtime._components.policy_engine
    existing = getattr(old_engine, "policies", ())
    merged = tuple(existing) + tuple(extra_policies)
    new_engine = PolicyEngine(merged)
    new_components = dc_replace(runtime._components, policy_engine=new_engine)
    runtime._components = new_components
    return service


def _build_probe_service(profile_config: str) -> RuntimeService:
    """Build a probe-style RuntimeService (domain operator probe surface)."""

    from universal_agent.domains.profile_service import build_probe_service

    return build_probe_service(profile_config)


def _resolve_agentd_auth_token(
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


def _host_requires_auth(host: str) -> bool:
    normalized = parse_non_empty_string(host, "agentd host").strip().lower()
    return normalized not in {"127.0.0.1", "localhost", "::1"}


def _build_admin_store(args: argparse.Namespace) -> CredentialAdminStore | None:
    """Build the optional admin credential store from server flags.

    memory: in-process, not durable (dev/test). postgres: durable schema v3
    principal tables; the DSN is read from the environment variable named by
    ``--admin-store-url-env`` (never from config files or argv).
    """

    admin_store = getattr(args, "admin_store", None)
    url_env = getattr(args, "admin_store_url_env", None)
    if admin_store is None:
        if url_env is not None:
            raise ValueError("--admin-store-url-env requires --admin-store postgres")
        return None
    if admin_store == "memory":
        from universal_agent.security import InMemoryCredentialStore

        return InMemoryCredentialStore()
    if url_env is None:
        raise ValueError("--admin-store postgres requires --admin-store-url-env")
    url = os.environ.get(url_env)
    if not url:
        raise ValueError(
            f"admin store url_env {url_env!r} is not set in the environment; "
            "export the Postgres DSN"
        )
    try:
        from universal_agent.persistence.credentials import PostgresCredentialStore
    except ImportError as exc:
        raise ValueError(
            "--admin-store postgres requires the optional 'postgres' extra: "
            "pip install 'universal-agent-runtime[postgres]'"
        ) from exc
    return PostgresCredentialStore(url)


def _build_audit_recorder(args: argparse.Namespace, *, admin_store: object) -> AuditRecorder | None:
    """Build the security audit sink: JSONL file when configured, otherwise an
    in-memory ring when the admin plane is enabled, else disabled."""

    audit_log = getattr(args, "audit_log", None)
    if audit_log is not None:
        from universal_agent.security import FileAuditRecorder

        return FileAuditRecorder(audit_log)
    if admin_store is not None:
        from universal_agent.security import InMemoryAuditRecorder

        return InMemoryAuditRecorder()
    return None


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    auth_token = _resolve_agentd_auth_token(
        explicit=args.auth_token,
        env_key=args.auth_token_env,
        label="auth token",
    )
    read_only_auth_token = _resolve_agentd_auth_token(
        explicit=args.read_only_auth_token,
        env_key=args.read_only_auth_token_env,
        label="read-only auth token",
    )
    if _host_requires_auth(args.host) and auth_token is None and read_only_auth_token is None:
        parser.error("agentd auth token is required when binding to non-loopback host")
    profile_config = args.profile_config
    # Load deployment config (declarative policies + preferences) BEFORE
    # building any service so policies flow into the PolicyEngine at assembly.
    deployment_config_path = (
        Path(os.environ.get("AGENT_CONFIG_DIR", "universal-agent")) / "deployment.json"
    )
    if args.deployment_config:
        deployment_config_path = Path(args.deployment_config)
    from universal_agent.deployment_config import DeploymentConfigStore

    config_store = DeploymentConfigStore(deployment_config_path)
    extra_policies = config_store.load_policies()

    profiles_dir = (
        args.profiles_dir
        or os.environ.get("AGENT_PROFILES_DIR")
        or str(Path(os.environ.get("AGENT_CONFIG_DIR", "universal-agent")) / "profiles")
    )
    profile_store = ProfileStore(profiles_dir)

    if profile_config is not None:
        service = (
            _build_probe_service(profile_config)
            if args.probe_only
            else _build_service_from_profile(profile_config, extra_policies=extra_policies)
        )
    else:
        service = _build_default_with_policies(args.default_domain, extra_policies)

    admin_store = _build_admin_store(args)
    audit_recorder = _build_audit_recorder(args, admin_store=admin_store)
    auth_policy = AgentdAuthPolicy(
        bearer_token=auth_token,
        read_only_bearer_token=read_only_auth_token,
        credential_store=admin_store,
        tenant_id=args.tenant_id,
    )

    server = AgentdHttpServer(
        AgentdApp(
            service,
            auth=auth_policy,
            evaluation_report_dir=args.evaluation_report_dir,
            profile_store=profile_store,
            profile_service_factory=_profile_service_factory(profile_store, extra_policies),
            admin_store=admin_store,
            audit_recorder=audit_recorder,
        ),
        AgentdServerConfig(host=args.host, port=args.port),
    )
    if args.port_file:
        Path(args.port_file).write_text(str(server.server_address[1]), encoding="utf-8")
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
