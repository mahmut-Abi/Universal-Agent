"""Remote eval and ecosystem command dispatch."""

from __future__ import annotations

import argparse
from typing import TextIO, cast

from universal_agent.core import JsonValue
from universal_agent_api import AgentdClient
from universal_agent_cli.io import CliExit, _write_json

_EVAL_REMOTE_OPERATIONS = {
    "list": "list",
    "run": "run",
    "replay": "replay",
    "recordings": "recordings",
    "compare": "compare",
    "reports": "reports",
    "datasets": "datasets",
    "dataset": "dataset",
}

_EVAL_REMOTE_FLOAT_FIELDS = (
    "min_pass_rate",
    "min_goal_completion_rate",
    "min_task_success_rate",
    "min_action_success_rate",
    "max_tool_failure_rate",
    "max_policy_denial_rate",
    "max_average_recoveries",
    "max_human_intervention_rate",
    "max_average_actions",
    "max_average_active_resource_locks",
    "max_average_duration_ms",
    "max_average_model_calls",
    "max_average_model_tokens",
    "max_resource_conflict_rate",
)

_ECOSYSTEM_REMOTE_OPERATIONS = {
    "catalog": "catalog",
    "verify": "verify",
    "export": "export",
    "registry": "registry",
    "install": "install",
    "store": "store",
}


def _remote_eval_body(args: argparse.Namespace) -> dict[str, JsonValue]:
    # recordings/compare/reports/datasets/dataset have no profile positional.
    body: dict[str, JsonValue] = {}
    profile = cast(str | None, getattr(args, "profile", None))
    if profile is not None:
        body["profile"] = profile
    suite = cast(str | None, getattr(args, "suite", None))
    if suite is not None:
        body["suite"] = suite
    for key in (
        "suite_file",
        "report_dir",
        "recording_dir",
        "expected",
        "actual",
        "dataset_dir",
        "domain",
        "name",
        "version",
    ):
        value = cast(str | None, getattr(args, key, None))
        if value is not None:
            body[key] = value
    for key in ("kind", "tag", "exclude_tag"):
        values = cast(list[str] | None, getattr(args, key, None))
        if values:
            body[key] = [str(item) for item in values]
    if cast(bool, getattr(args, "update", False)):
        body["update"] = True
    if cast(bool, getattr(args, "fail_on_fail", False)):
        body["fail_on_fail"] = True
    eval_format = cast(str | None, getattr(args, "format", None))
    if eval_format is not None:
        body["format"] = eval_format
    for key in _EVAL_REMOTE_FLOAT_FIELDS:
        float_value = cast(float | None, getattr(args, key, None))
        if float_value is not None:
            body[key] = float_value
    cost = cast(int | None, getattr(args, "max_total_model_cost_micros", None))
    if cost is not None:
        body["max_total_model_cost_micros"] = cost
    return body


def _remote_ecosystem_body(args: argparse.Namespace) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {}
    for key in (
        "domain_package_dir",
        "dataset_dir",
        "profile_dir",
        "name",
        "version",
        "description",
        "output",
        "manifest",
        "base_path",
        "store_dir",
    ):
        value = cast(str | None, getattr(args, key, None))
        if value is not None:
            body[key] = value
    for key in ("force", "verify", "no_verify", "plan_only", "allow_unverified_signatures"):
        if cast(bool, getattr(args, key, False)):
            body[key] = True
    store_command = cast(str | None, getattr(args, "ecosystem_store_command", None))
    if store_command is not None:
        body["store_command"] = store_command
    return body


async def _dispatch_remote_eval(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    from universal_agent_cli.io import _write_text

    eval_command = cast(str, args.eval_command)
    operation = _EVAL_REMOTE_OPERATIONS[eval_command]
    wants_junit = eval_command == "run" and cast(str, getattr(args, "format", "json")) == "junit"
    if wants_junit:
        response = await client.post_text(f"/v1/eval/{operation}", body=_remote_eval_body(args))
        _write_text(out, response.text)
        return
    payload = await client.post_json(f"/v1/eval/{operation}", body=_remote_eval_body(args))
    _write_json(out, payload)
    if cast(bool, getattr(args, "fail_on_fail", False)) and not payload.get("passed", True):
        raise CliExit(1)


async def _dispatch_remote_ecosystem(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    ecosystem_command = cast(str, args.ecosystem_command)
    operation = _ECOSYSTEM_REMOTE_OPERATIONS[ecosystem_command]
    payload = await client.post_json(
        f"/v1/ecosystem/{operation}",
        body=_remote_ecosystem_body(args),
    )
    error = payload.get("error")
    if isinstance(error, dict):
        _write_json(
            out,
            {
                "error": {
                    "code": "bad_request",
                    "message": str(error.get("message", "")),
                }
            },
        )
        raise CliExit(2)
    _write_json(out, payload)
