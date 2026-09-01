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
    parser.add_argument("--auth-token")
    parser.add_argument("--read-only-auth-token")
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


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    profile_config = args.profile_config
    if profile_config is not None:
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
                bearer_token=args.auth_token,
                read_only_bearer_token=args.read_only_auth_token,
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
