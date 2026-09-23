"""Remote run/session lifecycle dispatch."""

from __future__ import annotations

import argparse
import time
from collections.abc import Mapping
from typing import TextIO, cast

from universal_agent.core import JsonValue
from universal_agent_api import AgentdClient
from universal_agent_cli.io import (
    _success_criteria,
    _warn_mutation_goal_without_criteria,
    _write_json,
    _write_text,
)
from universal_agent_cli.remote._shared import success_criteria_body
from universal_agent_cli.text_views import render_run_text


async def _warn_if_mutation_goal_unfulfilled(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
    payload: Mapping[str, JsonValue],
) -> None:
    """UA-LIVE-2026-09-21 P10b hardening: after a completed run on a
    mutation-shaped goal, verify at least one side-effecting action ran."""

    import sys

    from universal_agent_cli.io import mutation_without_action_warning

    result = payload.get("result")
    session = payload.get("session")
    if not isinstance(result, dict) or not isinstance(session, dict):
        return
    if str(result.get("status")) != "completed":
        return
    session_id = str(session.get("session_id", ""))
    if not session_id:
        return
    events = await client.get_json(f"/v1/sessions/{session_id}/events", query={"limit": 500})
    event_items = events.get("events", [])
    if not isinstance(event_items, list):
        return
    side_effects = {
        str(item.get("side_effect"))
        for item in event_items
        if isinstance(item, dict)
        and item.get("type") == "ActionStarted"
        and isinstance(item.get("data"), dict)
    }
    warning = mutation_without_action_warning(
        cast(str, args.goal),
        session_completed=True,
        side_effects=side_effects,
    )
    if warning is not None:
        sys.stderr.write(warning + "\n")


async def _dispatch_remote_run(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    from universal_agent_cli.io import mutation_goal_criteria_error

    rejection = mutation_goal_criteria_error(
        cast(str, args.goal),
        cast(list[str], args.success),
        dry_run=cast(bool, getattr(args, "dry_run", False)),
        allow_unverified=cast(bool, getattr(args, "allow_unverified_mutation", False)),
    )
    if rejection is not None:
        raise ValueError(rejection)
    _warn_mutation_goal_without_criteria(cast(str, args.goal), cast(list[str], args.success))
    criteria = _success_criteria(cast(list[str], args.success))
    # The profile is optional in Golden Path runs; the runtime selects its
    # primary profile when the body omits it.
    profile = await _resolve_remote_run_profile(args, client)
    body: dict[str, JsonValue] = {
        "goal": {
            "description": cast(str, args.goal),
            "success_criteria": success_criteria_body(criteria),
        },
    }
    if profile is not None:
        body["profile"] = profile
    if cast(bool, args.compile_goal):
        if cast(str | None, args.task) is not None:
            raise ValueError("--task cannot be used with --compile-goal")
        body["compile_goal"] = True
    else:
        body["task"] = {
            "description": cast(str | None, args.task) or "Run goal",
            "required_criteria": [item.key for item in criteria],
        }
    timeout_seconds = cast(float | None, getattr(args, "timeout_seconds", None))
    if timeout_seconds is not None:
        if timeout_seconds <= 0:
            raise ValueError("--timeout-seconds must be greater than 0")
        body["timeout_seconds"] = timeout_seconds
    if cast(bool, getattr(args, "dry_run", False)):
        body["read_only"] = True
    started = time.monotonic()
    payload = await client.post_json("/v1/sessions", body=body)
    await _warn_if_mutation_goal_unfulfilled(args, out, client, payload)
    duration_seconds = time.monotonic() - started
    if cast(str, args.output) == "text":
        events_body = await _remote_run_events(payload, client)
        _write_text(
            out,
            render_run_text(
                payload,
                duration_seconds=duration_seconds,
                events_body=events_body,
                profile=profile,
            ),
        )
        return
    _write_json(out, payload)


async def _remote_run_events(
    run_payload: Mapping[str, JsonValue],
    client: AgentdClient,
) -> dict[str, JsonValue] | None:
    result = run_payload.get("result")
    if not isinstance(result, Mapping):
        return None
    session_id = result.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    batch = await client.get_json(
        f"/v1/sessions/{session_id}/events",
        query={"limit": 500},
    )
    return dict(batch)


async def _resolve_remote_run_profile(
    args: argparse.Namespace,
    client: AgentdClient,
) -> str | None:
    flag = cast(str | None, getattr(args, "profile_option", None))
    positional = cast(str | None, getattr(args, "profile", None))
    if flag is not None and positional is not None and flag != positional:
        raise ValueError(
            "run accepts one profile: use --profile or the positional argument, not both"
        )
    selected = flag if flag is not None else positional
    if selected is not None:
        return selected
    if not cast(bool, getattr(args, "resolve_default_profile", True)):
        return None
    catalog = await client.get_json("/v1/profiles")
    profiles = catalog.get("profiles")
    if isinstance(profiles, list):
        for item in profiles:
            if isinstance(item, Mapping) and item.get("name"):
                return str(item["name"])
    return None
