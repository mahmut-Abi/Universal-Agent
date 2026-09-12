"""Remote session command dispatch."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import TextIO, cast

from universal_agent.core import EventId, JsonMapping, JsonValue
from universal_agent.core.config_validation import parse_bounded_float
from universal_agent.core.polling import poll_async_result
from universal_agent_api import AgentdClient, quote_path_segment
from universal_agent_cli.io import _optional_bool, _write_json, _write_text
from universal_agent_cli.text_views import (
    render_session_explain_text,
    render_session_list_text,
    render_session_not_found_explain_text,
    render_session_show_text,
)


async def _dispatch_remote_session(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    session_command = cast(str, args.session_command)
    if session_command == "list":
        body = await client.get_json(
            "/v1/sessions",
            query={
                "after": cast(str | None, args.after),
                "limit": cast(int | None, args.limit),
            },
        )
        if cast(str, args.output) == "text":
            _write_text(out, render_session_list_text(body))
            return
        _write_json(out, body)
        return
    if session_command == "show":
        await _write_remote_session_json(args, out, client, "")
        return
    if session_command == "explain":
        await _write_remote_session_explain(args, out, client)
        return
    if session_command == "diagnostics":
        await _write_remote_session_json(args, out, client, "diagnostics")
        return
    if session_command == "evidence":
        await _write_remote_session_json(args, out, client, "evidence")
        return
    if session_command == "world":
        await _write_remote_session_json(
            args,
            out,
            client,
            "world",
            query={
                "entity_id": cast(str | None, args.entity),
                "relation": cast(str | None, args.relation),
            },
        )
        return
    if session_command == "events":
        await _dispatch_remote_session_events(args, out, client)
        return
    if session_command in {"audit", "cost", "logs"}:
        if session_command == "audit" and cast(bool, args.integrity):
            await _write_remote_session_json(args, out, client, "audit/integrity")
            return
        await _write_remote_session_json(args, out, client, session_command)
        return
    if session_command == "traces":
        suffix = "traces/otlp" if cast(str, args.format) == "otlp" else "traces"
        await _write_remote_session_json(args, out, client, suffix)
        return
    if session_command == "pause":
        await _post_remote_session_action(
            args,
            out,
            client,
            "pause",
            {"reason": cast(str, args.reason)},
        )
        return
    if session_command == "resume":
        confirmed = _optional_bool(cast(str | None, args.confirmed))
        body = {} if confirmed is None else {"confirmed": confirmed}
        await _post_remote_session_action(args, out, client, "resume", body)
        return
    if session_command == "cancel":
        await _post_remote_session_action(
            args,
            out,
            client,
            "cancel",
            {"reason": cast(str, args.reason)},
        )
        return
    raise ValueError(f"unknown session command: {session_command}")


async def _write_remote_session_explain(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    session_id = quote_path_segment(cast(str, args.session_id))
    try:
        payload = await client.get_json(f"/v1/sessions/{session_id}")
        events = await client.get_json(
            f"/v1/sessions/{session_id}/events",
            query={"limit": 500},
        )
    except Exception:
        if cast(str, args.output) == "text":
            _write_text(out, render_session_not_found_explain_text(cast(str, args.session_id)))
            return
        _write_json(
            out,
            {
                "error": "session_not_found",
                "reason": f"No persisted session exists with id {args.session_id}.",
                "try": "Run `agent session list` or pass the same --profile-config.",
                "session_id": cast(str, args.session_id),
            },
        )
        return
    if cast(str, args.output) == "text":
        _write_text(out, render_session_explain_text(payload, events))
        return
    _write_json(
        out,
        {
            "session_id": cast(str, args.session_id),
            "status": payload.get("goal_status"),
            "termination_reason": payload.get("termination_reason"),
            "pending_action": payload.get("pending_action"),
        },
    )


async def _write_remote_session_json(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
    suffix: str,
    *,
    query: Mapping[str, object] | None = None,
) -> None:
    session_id = quote_path_segment(cast(str, args.session_id))
    path = f"/v1/sessions/{session_id}"
    if suffix:
        path += f"/{suffix}"
    payload = await client.get_json(path, query=query)
    if suffix == "" and cast(str, getattr(args, "output", "json")) == "text":
        events = await client.get_json(
            f"/v1/sessions/{session_id}/events",
            query={"limit": 500},
        )
        _write_text(out, render_session_show_text(payload, events))
        return
    _write_json(out, payload)


async def _dispatch_remote_session_events(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    if cast(str, args.format) == "sse":
        response = await client.get_text(
            f"/v1/sessions/{quote_path_segment(cast(str, args.session_id))}/events/stream",
            query=_session_events_query(args, include_wait=True),
        )
        _write_text(out, response.text)
        return
    batch = await _remote_event_batch(args, client)
    _write_json(out, batch)


async def _remote_event_batch(args: argparse.Namespace, client: AgentdClient) -> JsonMapping:
    path = f"/v1/sessions/{quote_path_segment(cast(str, args.session_id))}/events"
    query = _session_events_query(args, include_wait=False)
    if not cast(bool, args.wait):
        return await client.get_json(path, query=query)
    timeout_seconds = parse_bounded_float(
        cast(float, args.timeout_seconds),
        "timeout_seconds",
        minimum=0.0,
        maximum=30.0,
    )
    poll_interval_seconds = parse_bounded_float(
        cast(float, args.poll_interval_seconds),
        "poll_interval_seconds",
        minimum=0.001,
        maximum=5.0,
    )
    return await poll_async_result(
        lambda: client.get_json(path, query=query),
        retry_if=lambda batch: not _has_events(batch),
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def _session_events_query(
    args: argparse.Namespace,
    *,
    include_wait: bool,
) -> dict[str, object | None]:
    after = cast(str | None, args.after)
    query: dict[str, object | None] = {
        "after": None if after is None else str(EventId(after)),
        "limit": cast(int | None, args.limit),
    }
    if include_wait:
        query.update(
            {
                "wait": cast(bool, args.wait),
                "timeout_seconds": cast(float, args.timeout_seconds),
                "poll_interval_seconds": cast(float, args.poll_interval_seconds),
            }
        )
    return query


def _has_events(batch: JsonMapping) -> bool:
    events = batch.get("events")
    return isinstance(events, list) and bool(events)


async def _post_remote_session_action(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
    action: str,
    body: Mapping[str, JsonValue],
) -> None:
    session_id = quote_path_segment(cast(str, args.session_id))
    payload = await client.post_json(f"/v1/sessions/{session_id}/{action}", body=dict(body))
    if cast(str, getattr(args, "output", "json")) == "text":
        result = payload.get("result")
        if isinstance(result, Mapping):
            status = str(result.get("status", ""))
            reason = str(result.get("reason") or "")
            error_code = result.get("error_code")
            lines = [f"Session: {result.get('session_id', '')}", f"Status: {status}"]
            if reason:
                lines.append(f"Reason: {reason}")
            if status == "failed" and error_code == "invalid_state":
                lines.append(
                    "Try: `agent session list` — the session already reached a "
                    "terminal state and cannot change further."
                )
            elif status == "waiting":
                lines.append(
                    f"Try: `agent session resume {args.session_id} --confirmed true` "
                    "to approve the pending action."
                )
            _write_text(out, "\n".join(lines) + "\n")
            return
    _write_json(out, payload)
