"""Module entry point for the agentd HTTP server.

Allows the client packages to launch an embedded runtime as a subprocess
(process-isolated HTTP communication) without importing kernel internals:

    python -m universal_agent.agentd --port 0 --port-file /tmp/agentd.port

The service is built from the same profile-config machinery the CLI uses.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from universal_agent.agentd.app import AgentdApp
from universal_agent.agentd.http import AgentdAuthPolicy
from universal_agent.agentd.server import AgentdHttpServer, AgentdServerConfig
from universal_agent.core.config_validation import parse_non_empty_string
from universal_agent.domains.kubernetes.cli_runtime import (
    build_configured_service,
)
from universal_agent.profile import ProfileConfig
from universal_agent.security import EnvSecretProvider
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
    return parser


def _build_service_from_profile(profile_config: str) -> RuntimeService:
    """Build a RuntimeService from a profile config file.

    Mirrors the CLI's build semantics: profiles with domain_package_paths load
    their packaged domains; everything else uses the default Kubernetes build.
    """

    profile = ProfileConfig.from_json_file(profile_config).to_profile()
    if profile.runtime.domain_package_paths:
        from universal_agent.host import build_configured_model_adapter
        from universal_agent.host.runtime import RuntimeHost

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
    return build_configured_service(profile_config)


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
    if profile_config is not None:
        if args.probe_only:
            from universal_agent.domains.kubernetes.cli_runtime import (
                build_configured_probe_service,
            )

            service = build_configured_probe_service(profile_config)
        else:
            service = _build_service_from_profile(profile_config)
    else:
        from universal_agent.domains.kubernetes.cli_runtime import (
            build_default_service,
        )

        service = build_default_service()

    server = AgentdHttpServer(
        AgentdApp(
            service,
            auth=AgentdAuthPolicy(
                bearer_token=auth_token,
                read_only_bearer_token=read_only_auth_token,
            ),
            evaluation_report_dir=args.evaluation_report_dir,
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
