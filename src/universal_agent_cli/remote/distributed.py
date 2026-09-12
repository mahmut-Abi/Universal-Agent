"""Remote distributed command dispatch (advanced/experimental local primitives)."""

from __future__ import annotations

import argparse
from typing import TextIO, cast

from universal_agent.core import JsonValue
from universal_agent_api import AgentdClient, quote_path_segment
from universal_agent_cli.io import _success_criteria, _write_json
from universal_agent_cli.remote._shared import success_criteria_body


async def _dispatch_remote_distributed(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
) -> None:
    distributed_command = cast(str, args.distributed_command)
    if distributed_command == "snapshot":
        _write_json(out, await client.get_json("/v1/distributed/snapshot"))
        return
    if distributed_command == "health":
        _write_json(out, await client.get_json("/v1/distributed/health"))
        return
    if distributed_command == "expire":
        _write_json(out, await client.post_json("/v1/distributed/expire", body={}))
        return
    if distributed_command == "prune-terminal":
        body: dict[str, JsonValue] = {}
        before = cast(str | None, args.before)
        if before is not None:
            body["before"] = before
        _write_json(out, await client.post_json("/v1/distributed/prune-terminal", body=body))
        return
    if distributed_command == "schedule-session":
        session_id = quote_path_segment(str(SessionIdValue(cast(str, args.session_id))))
        _write_json(
            out,
            await client.post_json(
                f"/v1/distributed/sessions/{session_id}/schedule",
                body=_distributed_schedule_body(args),
            ),
        )
        return
    if distributed_command == "schedule-goal":
        _write_json(
            out,
            await client.post_json(
                "/v1/distributed/goals",
                body=_distributed_goal_schedule_body(args),
            ),
        )
        return
    if distributed_command == "schedule-task":
        session_id = quote_path_segment(str(SessionIdValue(cast(str, args.session_id))))
        task_id = quote_path_segment(str(TaskIdValue(cast(str, args.task_id))))
        _write_json(
            out,
            await client.post_json(
                f"/v1/distributed/sessions/{session_id}/tasks/{task_id}/schedule",
                body=_distributed_schedule_body(args),
            ),
        )
        return
    if distributed_command == "schedule-action":
        session_id = quote_path_segment(str(SessionIdValue(cast(str, args.session_id))))
        task_id = quote_path_segment(str(TaskIdValue(cast(str, args.task_id))))
        action_id = quote_path_segment(str(ActionIdValue(cast(str, args.action_id))))
        body = _distributed_schedule_body(args)
        body["confirmed"] = cast(str, args.confirmed) == "true"
        _write_json(
            out,
            await client.post_json(
                (
                    f"/v1/distributed/sessions/{session_id}/tasks/{task_id}/actions/"
                    f"{action_id}/schedule"
                ),
                body=body,
            ),
        )
        return
    if distributed_command == "schedule-pending-actions":
        body = _distributed_schedule_body(args)
        body["confirmed"] = cast(str, args.confirmed) == "true"
        _write_json(
            out,
            await client.post_json("/v1/distributed/pending-actions/schedule", body=body),
        )
        return
    if distributed_command == "cancel":
        work_item_id = quote_path_segment(cast(str, args.work_item_id))
        _write_json(
            out,
            await client.post_json(
                f"/v1/distributed/work-items/{work_item_id}/cancel",
                body={"reason": cast(str, args.reason)},
            ),
        )
        return
    if distributed_command == "worker-register":
        worker_id = quote_path_segment(cast(str, args.worker_id))
        _write_json(
            out,
            await client.post_json(
                f"/v1/distributed/workers/{worker_id}/register",
                body={
                    "capabilities": list(cast(list[str], args.capability)),
                    "ttl_seconds": cast(float, args.ttl_seconds),
                },
            ),
        )
        return
    if distributed_command == "worker-heartbeat":
        worker_id = quote_path_segment(cast(str, args.worker_id))
        _write_json(
            out,
            await client.post_json(
                f"/v1/distributed/workers/{worker_id}/heartbeat",
                body={"ttl_seconds": cast(float, args.ttl_seconds)},
            ),
        )
        return
    if distributed_command == "worker-run-once":
        worker_id = quote_path_segment(cast(str, args.worker_id))
        _write_json(
            out,
            await client.post_json(
                f"/v1/distributed/workers/{worker_id}/run-once",
                body=_distributed_worker_run_body(args),
            ),
        )
        return
    if distributed_command == "worker-run":
        worker_id = quote_path_segment(cast(str, args.worker_id))
        body = _distributed_worker_run_body(args)
        body["max_items"] = cast(int, args.max_items)
        _write_json(
            out,
            await client.post_json(
                f"/v1/distributed/workers/{worker_id}/run",
                body=body,
            ),
        )
        return
    if distributed_command == "worker-drain":
        await _post_remote_distributed_worker_reason(args, out, client, "drain")
        return
    if distributed_command == "worker-offline":
        await _post_remote_distributed_worker_reason(args, out, client, "offline")
        return
    if distributed_command == "lock-acquire":
        _write_json(
            out,
            await client.post_json(
                "/v1/distributed/locks/acquire",
                body={
                    "lock_key": cast(str, args.lock_key),
                    "owner_id": cast(str, args.owner_id),
                    "ttl_seconds": cast(float, args.ttl_seconds),
                },
            ),
        )
        return
    if distributed_command == "lock-heartbeat":
        lease_id = quote_path_segment(cast(str, args.lease_id))
        _write_json(
            out,
            await client.post_json(
                f"/v1/distributed/lock-leases/{lease_id}/heartbeat",
                body={
                    "owner_id": cast(str, args.owner_id),
                    "ttl_seconds": cast(float, args.ttl_seconds),
                },
            ),
        )
        return
    if distributed_command == "lock-release":
        lease_id = quote_path_segment(cast(str, args.lease_id))
        _write_json(
            out,
            await client.post_json(
                f"/v1/distributed/lock-leases/{lease_id}/release",
                body={"owner_id": cast(str, args.owner_id)},
            ),
        )
        return
    raise ValueError(f"distributed command does not support --api-url: {distributed_command}")


class SessionIdValue(str):
    """Marker string for session ids (kept local to avoid core import cycles)."""


class TaskIdValue(str):
    """Marker string for task ids."""


class ActionIdValue(str):
    """Marker string for action ids."""


def _distributed_schedule_body(args: argparse.Namespace) -> dict[str, JsonValue]:
    return {
        "priority": cast(int, args.priority),
        "max_attempts": cast(int, args.max_attempts),
    }


def _distributed_goal_schedule_body(args: argparse.Namespace) -> dict[str, JsonValue]:
    criteria = _success_criteria(cast(list[str], args.success))
    return {
        "profile": cast(str, args.profile),
        "goal": {
            "description": cast(str, args.goal),
            "success_criteria": success_criteria_body(criteria),
        },
        "task": {
            "description": cast(str, args.task),
            "required_criteria": [item.key for item in criteria],
        },
        **_distributed_schedule_body(args),
    }


def _distributed_worker_run_body(args: argparse.Namespace) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "lease_ttl_seconds": cast(float, args.lease_ttl_seconds),
        "worker_ttl_seconds": cast(float, args.worker_ttl_seconds),
    }
    heartbeat_interval = cast(float | None, args.heartbeat_interval_seconds)
    if heartbeat_interval is not None:
        body["heartbeat_interval_seconds"] = heartbeat_interval
    return body


async def _post_remote_distributed_worker_reason(
    args: argparse.Namespace,
    out: TextIO,
    client: AgentdClient,
    action: str,
) -> None:
    worker_id = quote_path_segment(cast(str, args.worker_id))
    _write_json(
        out,
        await client.post_json(
            f"/v1/distributed/workers/{worker_id}/{action}",
            body={"reason": cast(str, args.reason)},
        ),
    )
