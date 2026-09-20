"""Remote chat REPL: each line becomes a goal run over the agentd HTTP API.

Mirrors the embedded `_dispatch_chat` loop without importing the kernel: the
AgentdClient owns the wire, so chat works against a remote agentd — including
hot-swapped profiles via the X-Profile header (set from --profile or
AGENT_PROFILE in dispatch_agentd_cli).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import TextIO, cast

from universal_agent.core import JsonValue
from universal_agent_api.client import AgentdClient


async def dispatch_remote_chat(args: object, out: TextIO, client: AgentdClient) -> None:
    """Interactive conversation: each line becomes a goal run on the remote runtime."""

    profile = str(cast(object, getattr(args, "profile", "")) or "")
    show_events = bool(getattr(args, "show_events", False))
    header = f"Universal Agent chat — remote{f' · profile {profile}' if profile else ''}. "
    print(header + "Type a goal per line; /exit quits, /help shows help.", flush=True)
    while True:
        try:
            line = (await asyncio.to_thread(input, "you> ")).strip()
        except EOFError:
            break
        if not line:
            continue
        if line in {"/exit", "/quit", "exit", "quit"}:
            break
        if line == "/help":
            print("Type a goal per line. /exit quits. /help shows this.", flush=True)
            continue
        body: dict[str, JsonValue] = {
            "goal": {"description": line, "success_criteria": []},
            "task": {"description": "Chat turn", "required_criteria": []},
        }
        if profile:
            body["profile"] = profile
        try:
            payload = await client.post_json(
                "/v1/sessions", body=cast(Mapping[str, JsonValue], body)
            )
        except Exception as exc:
            print(f"[error] {exc}", flush=True)
            continue
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            print("[error] unexpected response payload", flush=True)
            continue
        status = str(result.get("status") or "unknown")
        reason = str(result.get("reason") or "")
        session_id = str(result.get("session_id") or "")
        print(
            f"[{status}] {reason}" + (f"  (session {session_id})" if session_id else ""), flush=True
        )
        if show_events and session_id:
            try:
                batch = await client.get_json(
                    f"/v1/sessions/{session_id}/events", query={"limit": 8}
                )
            except Exception:
                continue
            events = batch.get("events") if isinstance(batch, dict) else None
            event_list = events if isinstance(events, list) else []
            for event in event_list:
                if isinstance(event, dict):
                    print(f"  · {event.get('type')} {event.get('occurred_at', '')}", flush=True)
    print("bye", flush=True)
