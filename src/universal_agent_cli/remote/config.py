"""Remote config/profile-scope command dispatch."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TextIO, cast

from universal_agent.core import JsonMapping
from universal_agent_api import AgentdClient
from universal_agent_cli.io import _write_json, _write_text
from universal_agent_cli.text_views import render_config_text


async def _dispatch_remote_config(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    config_command = cast(str | None, args.config_command)
    if config_command in (None, "show"):
        body = await client.get_json("/v1/config")
        output = cast(str, getattr(args, "output", "json"))
        # Bare `agent config` defaults to text; `config show` defaults to JSON.
        wants_text = config_command is None or output == "text"
        if wants_text:
            _write_text(
                out,
                render_config_text(
                    body,
                    profile_config_path=_remote_config_scope_path(args),
                    config_dir=_remote_config_settings_dir(args),
                    active_profile=_remote_primary_profile_name(
                        await client.get_json("/v1/profiles")
                    ),
                    policies_body=await client.get_json("/v1/policies"),
                ),
            )
            return
        _write_json(out, body)
        return
    raise ValueError(f"unknown config command: {config_command}")


def _remote_primary_profile_name(profiles_body: JsonMapping) -> str | None:
    profiles = profiles_body.get("profiles")
    if not isinstance(profiles, list):
        return None
    for item in profiles:
        if isinstance(item, dict) and item.get("name"):
            return str(item["name"])
    return None


def _remote_config_settings_dir(args: argparse.Namespace) -> str | None:
    scope = _remote_config_scope_path(args)
    if scope is None:
        return None
    path = Path(scope).expanduser().parent
    return str(path) if path.joinpath("config.json").is_file() else None


def _remote_config_scope_path(args: argparse.Namespace) -> str | None:
    from universal_agent.profile import default_profile_config_path

    explicit = cast(str | None, args.profile_config)
    if explicit is not None:
        return explicit
    discovered = default_profile_config_path()
    return str(discovered) if discovered.is_file() else None
