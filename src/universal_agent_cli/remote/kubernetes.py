"""Remote Kubernetes operator command dispatch."""

from __future__ import annotations

import argparse
from typing import TextIO, cast

from universal_agent.core import JsonValue
from universal_agent_api import AgentdClient
from universal_agent_cli.io import CliExit, _write_json


async def _dispatch_remote_kubernetes(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    kubernetes_command = cast(str, args.kubernetes_command)
    operation = {
        "preflight": "preflight",
        "model-probe": "model-probe",
        "check": "check",
        "run": "run",
        "evidence": "evidence",
    }[kubernetes_command]
    body: dict[str, JsonValue] = {}
    workload = cast(str | None, getattr(args, "workload", None))
    if workload is not None:
        body["workload"] = workload
    # preflight has no profile positional; the operator commands do.
    profile = cast(str | None, getattr(args, "profile", None))
    if profile is not None:
        body["profile"] = profile
    # The profile config path is resolved on the agentd host (same machine for
    # the embedded runtime), so model/secret semantics match the local path.
    profile_config = cast(str | None, getattr(args, "profile_config", None))
    if profile_config is not None:
        body["profile_config"] = profile_config
    namespace = cast(str | None, args.namespace)
    if namespace is not None:
        body["namespace"] = namespace
    if kubernetes_command in {"preflight", "check", "run"}:
        body["skip_preflight"] = bool(getattr(args, "skip_preflight", False))
    if kubernetes_command in {"check", "run"}:
        body["skip_model_probe"] = bool(getattr(args, "skip_model_probe", False))
    if kubernetes_command in {"preflight", "check", "run", "evidence"}:
        body["skip_cluster"] = bool(getattr(args, "skip_cluster", False))
    if kubernetes_command == "evidence":
        body["submit_run"] = bool(getattr(args, "submit_run", False))
    if kubernetes_command == "run" and bool(getattr(args, "dry_run", False)):
        body["read_only"] = True

    payload = await client.post_json(f"/v1/kubernetes/{operation}", body=body)
    _write_json(out, payload)
    if str(payload.get("status")) == "failed":
        raise CliExit(1)
