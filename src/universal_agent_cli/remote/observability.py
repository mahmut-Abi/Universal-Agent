"""Remote observability/repair command dispatch."""

from __future__ import annotations

import argparse
from typing import TextIO, cast

from universal_agent_api import AgentdClient
from universal_agent_cli.io import _write_json, _write_text


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


async def _dispatch_remote_repair(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    repair_command = cast(str, args.repair_command)
    if repair_command == "state-events":
        _write_json(
            out,
            await client.post_json(
                "/v1/doctor/state-events/repair",
                body={
                    "confirmed": cast(str, args.confirmed) == "true",
                    "dry_run": cast(bool, args.dry_run),
                },
            ),
        )
        return
    raise ValueError(f"repair command does not support --api-url: {repair_command}")
