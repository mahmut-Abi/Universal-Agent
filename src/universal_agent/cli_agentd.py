from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import TextIO, cast

from universal_agent.agentd.client import AgentdClient, quote_path_segment
from universal_agent.cli_io import _optional_bool, _success_criteria, _write_json, _write_text
from universal_agent.core import EventId, JsonMapping, JsonValue, SuccessCriterion
from universal_agent.core.config_validation import parse_bounded_float
from universal_agent.core.polling import poll_async_result
from universal_agent.security import EnvSecretProvider

_REMOTE_STATIC_JSON_ROUTES: Mapping[str, str] = {
    "health": "/health",
    "ready": "/ready",
    "cost": "/v1/cost",
    "logs": "/v1/logs",
    "doctor": "/v1/doctor",
    "audit": "/v1/audit",
    "multi-agent": "/v1/multi-agent",
}

_REMOTE_LIST_ROUTES: Mapping[str, str] = {
    "domain": "/v1/domains",
    "capabilities": "/v1/capabilities",
    "tools": "/v1/tools",
    "policies": "/v1/policies",
    "evaluators": "/v1/evaluators",
    "memory": "/v1/memory",
}


async def dispatch_agentd_cli(args: argparse.Namespace, out: TextIO) -> None:
    async with AgentdClient(
        cast(str, args.api_url),
        bearer_token=_agentd_api_token(args),
    ) as client:
        command = cast(str, args.command)
        if command in _REMOTE_STATIC_JSON_ROUTES:
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


def command_supports_agentd(args: argparse.Namespace) -> bool:
    command = cast(str, args.command)
    if command in {
        "init",
        "serve",
        "version",
        "kubernetes",
        "tui",
        "ecosystem",
        "eval",
        "repair",
        "distributed",
    }:
        return False
    if command == "profile":
        return cast(str, args.profile_command) in {"list", "show"}
    if command == "domain-packages":
        return cast(str, args.domain_packages_command) in {"list", "show"}
    return True


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


async def _dispatch_remote_metrics(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    if cast(str, args.format) == "prometheus":
        response = await client.get_text("/v1/metrics/prometheus")
        _write_text(out, response.text)
        return
    _write_json(out, await client.get_json("/v1/metrics"))


async def _dispatch_remote_traces(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    if cast(str, args.format) == "otlp":
        _write_json(out, await client.get_json("/v1/traces/otlp"))
        return
    _write_json(out, await client.get_json("/v1/traces"))


async def _dispatch_remote_config(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    config_command = cast(str, args.config_command)
    if config_command == "show":
        _write_json(out, await client.get_json("/v1/config"))
        return
    raise ValueError(f"unknown config command: {config_command}")


async def _dispatch_remote_run(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    criteria = _success_criteria(cast(list[str], args.success))
    body: dict[str, JsonValue] = {
        "profile": cast(str, args.profile),
        "goal": {
            "description": cast(str, args.goal),
            "success_criteria": _success_criteria_body(criteria),
        },
        "task": {
            "description": cast(str, args.task),
            "required_criteria": [item.key for item in criteria],
        },
    }
    _write_json(out, await client.post_json("/v1/sessions", body=body))


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
        _write_json(out, await client.get_json("/v1/profiles"))
        return
    if profile_command == "show":
        profile = quote_path_segment(cast(str, args.profile))
        _write_json(out, await client.get_json(f"/v1/profiles/{profile}"))
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


async def _dispatch_remote_session(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    session_command = cast(str, args.session_command)
    if session_command == "list":
        _write_json(
            out,
            await client.get_json(
                "/v1/sessions",
                query={
                    "after": cast(str | None, args.after),
                    "limit": cast(int | None, args.limit),
                },
            ),
        )
        return
    if session_command == "show":
        await _write_remote_session_json(args, out, client, "")
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
    _write_json(out, await client.get_json(path, query=query))


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
    _write_json(out, await client.post_json(f"/v1/sessions/{session_id}/{action}", body=body))


def _success_criteria_body(criteria: tuple[SuccessCriterion, ...]) -> list[JsonValue]:
    return [{"key": item.key, "expected": item.expected} for item in criteria]
