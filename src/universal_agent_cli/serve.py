from __future__ import annotations

import argparse
import errno
from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import TextIO, cast

from universal_agent.agentd.app import AgentdApp
from universal_agent.agentd.http import AgentdAuthPolicy
from universal_agent.agentd.server import AgentdHttpServer, AgentdServerConfig
from universal_agent.security import EnvSecretProvider
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
