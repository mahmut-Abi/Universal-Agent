"""Remote (agentd thin-client) client setup and command router.

This module owns the client seams: bearer-token resolution, the top-level
command router, and the capability matrix for which commands support
``--api-url`` / embedded agentd dispatch. Command implementations live in
sibling modules under :mod:`universal_agent_cli.remote`.
"""

from __future__ import annotations

import argparse
from typing import TextIO, cast

from universal_agent.core import SessionId
from universal_agent.security import EnvSecretProvider
from universal_agent_api import AgentdClient
from universal_agent_cli.io import CliExit, _doctor_should_fail, _write_json
from universal_agent_cli.remote._shared import (
    REMOTE_LIST_ROUTES as _REMOTE_LIST_ROUTES,
)
from universal_agent_cli.remote._shared import (
    REMOTE_STATIC_JSON_ROUTES as _REMOTE_STATIC_JSON_ROUTES,
)
from universal_agent_cli.remote.catalog import (
    _dispatch_remote_domain_packages,
    _dispatch_remote_list_command,
    _dispatch_remote_profile,
)
from universal_agent_cli.remote.config import _dispatch_remote_config
from universal_agent_cli.remote.distributed import _dispatch_remote_distributed
from universal_agent_cli.remote.eval_ecosystem import (
    _dispatch_remote_ecosystem,
    _dispatch_remote_eval,
)
from universal_agent_cli.remote.observability import (
    _dispatch_remote_metrics,
    _dispatch_remote_repair,
    _dispatch_remote_traces,
)
from universal_agent_cli.remote.run import _dispatch_remote_run
from universal_agent_cli.remote.session import _dispatch_remote_session


async def dispatch_agentd_cli(args: argparse.Namespace, out: TextIO) -> None:
    """Production entry: connect to --api-url (or an embedded runtime) over HTTP."""

    async with AgentdClient(
        cast(str, args.api_url),
        bearer_token=_agentd_api_token(args),
        timeout_seconds=_client_timeout_seconds(args),
        extra_headers=_profile_headers(args),
    ) as client:
        await dispatch_agentd_commands(args, out, client)


def _profile_headers(args: argparse.Namespace) -> dict[str, str]:
    """X-Profile routing header for hot-swapped profiles.

    Sources, in order: the command's ``--profile`` flag (run/chat) and the
    ``AGENT_PROFILE`` environment variable (works for every command, e.g.
    ``AGENT_PROFILE=workspace agent session list``). Empty/absent means the
    agentd startup profile, matching the server's default routing.
    """

    import os

    selected = (
        getattr(args, "profile_option", None)
        or getattr(args, "profile", None)
        or os.environ.get("AGENT_PROFILE", "")
    )
    selected = str(selected or "").strip()
    if not selected:
        return {}
    return {"X-Profile": selected}


async def dispatch_agentd_commands(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    """Dispatch agentd-backed commands against an explicit client."""

    command = cast(str, args.command)
    if command == "doctor":
        payload = await client.get_json(_REMOTE_STATIC_JSON_ROUTES[command])
        _write_json(out, payload)
        if _doctor_should_fail(str(payload.get("status") or ""), cast(str, args.fail_on)):
            raise CliExit(1)
        return
    if command == "tui":
        await _dispatch_remote_tui(args, client)
        return
    if command == "chat":
        from universal_agent_cli.remote.chat import dispatch_remote_chat

        await dispatch_remote_chat(args, out, client)
        return
    from universal_agent_cli.parser import domain_contribution_for_command

    contribution = domain_contribution_for_command(command)
    if contribution is not None and contribution.remote_dispatch is not None:
        outcome = await contribution.remote_dispatch(args, client)
        _write_json(out, outcome.payload)
        if outcome.status != 0:
            raise CliExit(outcome.status)
        return
    if command == "eval":
        await _dispatch_remote_eval(args, out, client)
        return
    if command == "ecosystem":
        await _dispatch_remote_ecosystem(args, out, client)
        return
    if command in _REMOTE_STATIC_JSON_ROUTES:
        if command == "audit" and cast(bool, args.integrity):
            _write_json(out, await client.get_json("/v1/audit/integrity"))
            return
        _write_json(out, await client.get_json(_REMOTE_STATIC_JSON_ROUTES[command]))
        return
    if command == "metrics":
        await _dispatch_remote_metrics(args, out, client)
        return
    if command == "traces":
        await _dispatch_remote_traces(args, out, client)
        return
    if command == "config":
        await _dispatch_remote_config(args, out, client)
        return
    if command == "repair":
        await _dispatch_remote_repair(args, out, client)
        return
    if command == "distributed":
        await _dispatch_remote_distributed(args, out, client)
        return
    if command == "run":
        await _dispatch_remote_run(args, out, client)
        return
    if command in _REMOTE_LIST_ROUTES:
        await _dispatch_remote_list_command(args, out, client, command)
        return
    if command == "profile":
        await _dispatch_remote_profile(args, out, client)
        return
    if command == "domain-packages":
        await _dispatch_remote_domain_packages(args, out, client)
        return
    if command == "session":
        await _dispatch_remote_session(args, out, client)
        return
    raise ValueError(f"command does not support --api-url: {command}")


async def _dispatch_remote_tui(args: argparse.Namespace, client: AgentdClient) -> None:
    """Run the interactive TUI dashboard against a remote agentd Runtime API."""

    try:
        from universal_agent_tui.tui_app import RuntimeTuiApp
        from universal_agent_tui.tui_remote import (
            agentd_event_watcher,
            agentd_snapshot_provider,
            agentd_tui_actions,
        )
    except ImportError as exc:
        raise ImportError(
            "The interactive TUI requires the optional 'textual' dependency. "
            "Install it with: pip install 'universal-agent-runtime[tui]'"
        ) from exc

    session_id = cast(str | None, args.session_id)
    app = RuntimeTuiApp(
        snapshot_provider=agentd_snapshot_provider(
            client,
            session_limit=cast(int, args.session_limit),
            event_limit=cast(int, args.event_limit),
        ),
        session_id=SessionId(session_id) if session_id is not None else None,
        actions=agentd_tui_actions(client),
        event_watcher=agentd_event_watcher(client),
    )
    await app.run_async()


def command_supports_agentd(args: argparse.Namespace) -> bool:
    command = cast(str, args.command)
    if command in {
        "init",
        "serve",
        "version",
    }:
        return False
    if command == "repair":
        return cast(str, args.repair_command) == "state-events"
    if command == "config":
        return cast(str | None, args.config_command) in (None, "show")
    if command == "profile":
        return cast(str, args.profile_command) in {"list", "show"}
    if command == "domain-packages":
        return cast(str, args.domain_packages_command) in {"list", "show"}
    if command == "chat":
        return True
    return True


_BASE_LONG_RUN_COMMANDS = frozenset({"run", "eval"})
_LONG_RUN_DEFAULT_TIMEOUT_SECONDS = 900.0


def _contributed_long_run_commands() -> frozenset[str]:
    """Domain-contributed commands that use the long agentd client timeout."""

    from universal_agent_cli.contributions import load_cli_contributions

    return frozenset(
        contribution.command_name
        for contribution in load_cli_contributions()
        if contribution.command_name is not None and contribution.long_running_command
    )


def _client_timeout_seconds(args: argparse.Namespace) -> float:
    """Resolve the agentd request timeout for this CLI invocation.

    Long-running commands (goal execution with real model rounds) default to a
    900s request timeout so the client does not give up while the server-side
    session is still progressing; an explicit --api-timeout-seconds always
    wins.
    """
    explicit = cast(float | None, getattr(args, "api_timeout_seconds", None))
    if explicit is not None:
        return explicit
    if cast(str, args.command) in _BASE_LONG_RUN_COMMANDS | _contributed_long_run_commands():
        return _LONG_RUN_DEFAULT_TIMEOUT_SECONDS
    return 30.0


def _agentd_api_token(args: argparse.Namespace) -> str | None:
    explicit = cast(str | None, args.api_token)
    env_key = cast(str | None, args.api_token_env)
    if explicit is not None and env_key is not None:
        raise ValueError("agentd api token accepts either a literal value or env key, not both")
    if explicit is not None:
        return explicit
    if env_key is None:
        return None
    token = EnvSecretProvider().get_secret(env_key)
    if token is None:
        raise ValueError(f"agentd api token env key is missing or empty: {env_key}")
    return token
