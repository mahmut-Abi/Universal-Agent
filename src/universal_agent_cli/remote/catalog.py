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
    if command == "memory":
        await _dispatch_remote_memory(args, out, client, list_command)
        return
    raise ValueError(f"unknown {command} command: {list_command}")


async def _dispatch_remote_memory(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
    list_command: str,
) -> None:
    """memory add/get/delete against the agentd memory routes.

    The agentd server implements POST /v1/memory, GET/DELETE
    /v1/memory/{id}; the local injected dispatch implements the same
    surface, so remote parity was missing (UA-LIVE-2026-09-21 Q5).
    """

    if list_command == "add":
        body = {
            "kind": cast(str, args.kind),
            "subject": cast(str, args.subject),
            "content": cast(str, args.content),
            "scope": cast(str | None, getattr(args, "scope", None)) or "",
            "confidence": cast(float, getattr(args, "confidence", 1.0)),
        }
        _write_json(out, await client.post_json("/v1/memory", body=body))
        return
    memory_id = cast(str, args.memory_id)
    if list_command == "get":
        _write_json(out, await client.get_json(f"/v1/memory/{quote_path_segment(memory_id)}"))
        return
    if list_command == "delete":
        _write_json(
            out,
            await client.delete_json(f"/v1/memory/{quote_path_segment(memory_id)}"),
        )
        return
    raise ValueError(f"unknown memory command: {list_command}")


async def _dispatch_remote_profile(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    profile_command = cast(str, args.profile_command)
    if profile_command == "list":
        body = await client.get_json("/v1/profiles")
        # Merge store-backed profiles (created via the config API, not yet
        # loaded into the running service) so the full set is discoverable
        # (UA-LIVE-2026-09-21 R5-4).
        stored = await client.get_json("/v1/config/profiles")
        stored_names = [item for item in stored.get("stored_profiles", []) if isinstance(item, str)]
        loaded_names = [
            item.get("name", "") for item in body.get("profiles", []) if isinstance(item, dict)
        ]
        extra = [name for name in stored_names if name not in loaded_names]
        if extra:
            for item in extra:
                body["profiles"].append({"name": item, "stored_only": True})
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
    if profile_command == "create":
        from universal_agent.core import read_json_file

        source = cast(str, args.from_file)
        payload = read_json_file(source)
        _write_json(out, await client.post_json("/v1/profiles", body=payload))
        return
    if profile_command == "delete":
        profile = quote_path_segment(cast(str, args.profile))
        _write_json(
            out,
            await client.delete_json(f"/v1/profiles/{profile}"),
        )
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
