from __future__ import annotations

import argparse
import errno
import os
from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import TextIO, cast

from universal_agent.agentd.app import AgentdApp
from universal_agent.agentd.http import AgentdAuthPolicy
from universal_agent.agentd.server import AgentdHttpServer, AgentdServerConfig
from universal_agent.core.config_validation import parse_non_empty_string
from universal_agent.profile.store import ProfileStore
from universal_agent.service import RuntimeService
from universal_agent_cli.io import _write_json

ServerRunner = Callable[[AgentdHttpServer], Awaitable[None] | None]


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
    if _host_requires_auth(host) and auth_token is None and read_only_auth_token is None:
        raise ValueError("agentd auth token is required when binding to non-loopback host")
    from universal_agent.agentd.bootstrap import build_admin_store, build_audit_recorder

    admin_store = build_admin_store(
        cast(str | None, getattr(args, "admin_store", None)),
        cast(str | None, getattr(args, "admin_store_url_env", None)),
    )
    audit_recorder = build_audit_recorder(
        cast(str | None, getattr(args, "audit_log", None)),
        admin_store=admin_store,
    )
    tenant_id = cast(str | None, getattr(args, "tenant_id", None))
    if tenant_id is not None:
        tenant_id = parse_non_empty_string(tenant_id, "tenant_id")
    try:
        profile_store = _profiles_dir(args)
        server = AgentdHttpServer(
            AgentdApp(
                service,
                auth=AgentdAuthPolicy(
                    bearer_token=auth_token,
                    read_only_bearer_token=read_only_auth_token,
                    credential_store=admin_store,
                    tenant_id=tenant_id,
                ),
                admin_store=admin_store,
                audit_recorder=audit_recorder,
                evaluation_report_dir=cast(str | None, args.evaluation_report_dir),
                profile_store=profile_store,
                profile_service_factory=_profile_service_factory(profile_store),
            ),
            AgentdServerConfig(host=host, port=port),
        )
    except OSError as exc:
        raise ValueError(_serve_bind_error_message(host, port, exc)) from exc
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


def _serve_bind_error_message(host: str, port: int, exc: OSError) -> str:
    message = f"failed to bind agentd server on {host}:{port}: {exc}"
    if exc.errno == errno.EADDRINUSE or "Address already in use" in str(exc):
        return (
            f"{message}; another process is already listening on that address. "
            "Stop the existing server or retry with --port 0 / --port <free-port>."
        )
    return message


def _resolve_cli_auth_token(
    *,
    explicit: str | None,
    env_key: str | None,
    label: str,
) -> str | None:
    from universal_agent.agentd.bootstrap import resolve_auth_token

    return resolve_auth_token(explicit=explicit, env_key=env_key, label=label)


def _profiles_dir(args: object) -> ProfileStore | None:
    """Construct a ProfileStore from the profiles-dir CLI flag or defaults."""

    from pathlib import Path as _Path

    from universal_agent.profile.store import ProfileStore as _PS

    profiles_dir = getattr(args, "profiles_dir", None)
    if profiles_dir:
        return _PS(profiles_dir)
    config_dir = _Path(os.environ.get("AGENT_CONFIG_DIR", "universal-agent"))
    return _PS(config_dir / "profiles")


def _profile_service_factory(
    profile_store: ProfileStore | None,
) -> Callable[[str], RuntimeService] | None:
    """Lazy per-profile service builder for the hot-swap registry.

    Builds a RuntimeService from a persisted profile config the first time a
    request names it via the ``X-Profile`` header. Returns None when no
    profile store is configured, which disables hot-swap (the app answers
    with a structured bad_request).
    """

    if profile_store is None:
        return None

    def factory(profile_name: str) -> RuntimeService:
        from universal_agent.facade import build_configured_service

        config_path = profile_store.config_path(profile_name)
        if not config_path.is_file():
            from universal_agent.profile import ProfileConfigNotFoundError

            raise ProfileConfigNotFoundError(f"profile config not found: {profile_name}")
        return build_configured_service(str(config_path))

    return factory


def _host_requires_auth(host: str) -> bool:
    normalized = parse_non_empty_string(host, "agentd host").strip().lower()
    return normalized not in {"127.0.0.1", "localhost", "::1"}
