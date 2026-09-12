"""Remote catalog/list command dispatch: domain, profile, domain-packages."""

from __future__ import annotations

import argparse
from typing import TextIO, cast

from universal_agent_api import AgentdClient, quote_path_segment
from universal_agent_cli.io import _write_json, _write_text
from universal_agent_cli.remote._shared import REMOTE_LIST_ROUTES as _REMOTE_LIST_ROUTES
from universal_agent_cli.text_views import (
    render_profile_list_text,
    render_profile_show_text,
)


async def _dispatch_remote_list_command(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
    command: str,
) -> None:
    list_command = cast(str, getattr(args, f"{command.replace('-', '_')}_command"))
    if list_command == "list":
        _write_json(out, await client.get_json(_REMOTE_LIST_ROUTES[command]))
        return
    raise ValueError(f"unknown {command} command: {list_command}")


async def _dispatch_remote_profile(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    profile_command = cast(str, args.profile_command)
    if profile_command == "list":
        body = await client.get_json("/v1/profiles")
        if cast(str, getattr(args, "output", "json")) == "text":
            _write_text(out, render_profile_list_text(body))
            return
        _write_json(out, body)
        return
    if profile_command == "show":
        profile = quote_path_segment(cast(str, args.profile))
        body = await client.get_json(f"/v1/profiles/{profile}")
        if cast(str, getattr(args, "output", "json")) == "text":
            _write_text(
                out,
                render_profile_show_text(
                    body,
                    runtime_body=await client.get_json("/v1/config"),
                    policies_body=await client.get_json("/v1/policies"),
                ),
            )
            return
        _write_json(out, body)
        return
    raise ValueError("profile command does not support --api-url: " + profile_command)


async def _dispatch_remote_domain_packages(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    domain_packages_command = cast(str, args.domain_packages_command)
    if domain_packages_command == "list":
        _write_json(
            out,
            await client.get_json(
                "/v1/domain-packages",
                query={"tag": cast(str | None, args.tag)},
            ),
        )
        return
    if domain_packages_command == "show":
        name = quote_path_segment(cast(str, args.name))
        version = cast(str | None, args.version)
        path = f"/v1/domain-packages/{name}"
        if version is not None:
            path += "/" + quote_path_segment(version)
        _write_json(out, await client.get_json(path))
        return
    raise ValueError(
        "domain-packages command does not support --api-url: " + domain_packages_command
    )
