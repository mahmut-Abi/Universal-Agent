from __future__ import annotations

import argparse
from typing import TextIO, cast

from universal_agent.agentd.representations import (
    distributed_cancellation_body,
    distributed_health_body,
    distributed_lock_lifecycle_body,
    distributed_maintenance_body,
    distributed_pending_action_scheduling_body,
    distributed_prune_body,
    distributed_scheduling_body,
    distributed_snapshot_body,
    distributed_worker_lifecycle_body,
    distributed_worker_run_batch_body,
    distributed_worker_run_body,
)
from universal_agent.cli.io import (
    _parse_optional_datetime,
    _success_criteria,
    _write_json,
)
from universal_agent.core import ActionId, Goal, SessionId, Task, TaskId
from universal_agent.distributed import (
    DistributedLockLeaseId,
    DistributedLockOwnerId,
    WorkerId,
    WorkItemId,
)
from universal_agent.service import RuntimeService


async def _dispatch_distributed(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    distributed_command = cast(str, args.distributed_command)
    if distributed_command == "snapshot":
        snapshot = service.distributed_snapshot()
        if snapshot is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_snapshot_body(snapshot))
        return
    if distributed_command == "health":
        health = service.distributed_health()
        if health is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_health_body(health))
        return
    if distributed_command == "schedule-session":
        scheduling = service.distributed_schedule_session(
            SessionId(cast(str, args.session_id)),
            priority=cast(int, args.priority),
            max_attempts=cast(int, args.max_attempts),
        )
        if scheduling is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_scheduling_body(scheduling))
        return
    if distributed_command == "schedule-goal":
        profile = cast(str, args.profile)
        profile_error = service.profile_selection_error(profile)
        if profile_error is not None:
            raise ValueError(profile_error)
        criteria = _success_criteria(cast(list[str], args.success))
        goal = Goal(cast(str, args.goal), criteria)
        task = Task(cast(str, args.task), tuple(item.key for item in criteria))
        scheduling = service.distributed_schedule_goal(
            goal,
            task,
            priority=cast(int, args.priority),
            max_attempts=cast(int, args.max_attempts),
        )
        if scheduling is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_scheduling_body(scheduling))
        return
    if distributed_command == "schedule-task":
        scheduling = service.distributed_schedule_task(
            SessionId(cast(str, args.session_id)),
            TaskId(cast(str, args.task_id)),
            priority=cast(int, args.priority),
            max_attempts=cast(int, args.max_attempts),
        )
        if scheduling is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_scheduling_body(scheduling))
        return
    if distributed_command == "schedule-action":
        confirmed = cast(str, args.confirmed) == "true"
        if not confirmed:
            raise ValueError("distributed schedule-action requires --confirmed true")
        scheduling = service.distributed_schedule_action(
            SessionId(cast(str, args.session_id)),
            TaskId(cast(str, args.task_id)),
            ActionId(cast(str, args.action_id)),
            confirmed=confirmed,
            priority=cast(int, args.priority),
            max_attempts=cast(int, args.max_attempts),
        )
        if scheduling is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_scheduling_body(scheduling))
        return
    if distributed_command == "schedule-pending-actions":
        confirmed = cast(str, args.confirmed) == "true"
        if not confirmed:
            raise ValueError("distributed schedule-pending-actions requires --confirmed true")
        pending_scheduling = await service.distributed_schedule_pending_actions(
            confirmed=confirmed,
            priority=cast(int, args.priority),
            max_attempts=cast(int, args.max_attempts),
        )
        if pending_scheduling is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_pending_action_scheduling_body(pending_scheduling))
        return
    if distributed_command == "expire":
        maintenance = service.distributed_expire()
        if maintenance is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_maintenance_body(maintenance))
        return
    if distributed_command == "prune-terminal":
        pruned = service.distributed_prune_terminal_work_items(
            before=_parse_optional_datetime(cast(str | None, args.before))
        )
        if pruned is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_prune_body(pruned))
        return
    if distributed_command == "cancel":
        cancellation = service.distributed_cancel_work_item(
            WorkItemId(cast(str, args.work_item_id)),
            reason=cast(str, args.reason),
        )
        if cancellation is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_cancellation_body(cancellation))
        return
    if distributed_command == "worker-register":
        lifecycle = service.distributed_register_worker(
            WorkerId(cast(str, args.worker_id)),
            capabilities=tuple(cast(list[str], args.capability)),
            ttl_seconds=cast(float, args.ttl_seconds),
        )
        if lifecycle is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_worker_lifecycle_body(lifecycle))
        return
    if distributed_command == "worker-heartbeat":
        lifecycle = service.distributed_heartbeat_worker(
            WorkerId(cast(str, args.worker_id)),
            ttl_seconds=cast(float, args.ttl_seconds),
        )
        if lifecycle is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_worker_lifecycle_body(lifecycle))
        return
    if distributed_command == "worker-run-once":
        run = await service.distributed_run_worker_once(
            WorkerId(cast(str, args.worker_id)),
            lease_ttl_seconds=cast(float, args.lease_ttl_seconds),
            worker_ttl_seconds=cast(float, args.worker_ttl_seconds),
            heartbeat_interval_seconds=cast(float | None, args.heartbeat_interval_seconds),
        )
        if run is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_worker_run_body(run))
        return
    if distributed_command == "worker-run":
        runs = await service.distributed_run_worker_until_idle(
            WorkerId(cast(str, args.worker_id)),
            max_items=cast(int, args.max_items),
            lease_ttl_seconds=cast(float, args.lease_ttl_seconds),
            worker_ttl_seconds=cast(float, args.worker_ttl_seconds),
            heartbeat_interval_seconds=cast(float | None, args.heartbeat_interval_seconds),
        )
        if runs is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_worker_run_batch_body(runs))
        return
    if distributed_command == "worker-drain":
        lifecycle = service.distributed_drain_worker(
            WorkerId(cast(str, args.worker_id)),
            reason=cast(str, args.reason),
        )
        if lifecycle is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_worker_lifecycle_body(lifecycle))
        return
    if distributed_command == "worker-offline":
        lifecycle = service.distributed_mark_worker_offline(
            WorkerId(cast(str, args.worker_id)),
            reason=cast(str, args.reason),
        )
        if lifecycle is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_worker_lifecycle_body(lifecycle))
        return
    if distributed_command == "lock-acquire":
        lock_lifecycle = service.distributed_acquire_lock(
            lock_key=cast(str, args.lock_key),
            owner_id=DistributedLockOwnerId(cast(str, args.owner_id)),
            ttl_seconds=cast(float, args.ttl_seconds),
        )
        if lock_lifecycle is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_lock_lifecycle_body(lock_lifecycle))
        return
    if distributed_command == "lock-heartbeat":
        lock_lifecycle = service.distributed_heartbeat_lock(
            DistributedLockLeaseId(cast(str, args.lease_id)),
            owner_id=DistributedLockOwnerId(cast(str, args.owner_id)),
            ttl_seconds=cast(float, args.ttl_seconds),
        )
        if lock_lifecycle is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_lock_lifecycle_body(lock_lifecycle))
        return
    if distributed_command == "lock-release":
        lock_lifecycle = service.distributed_release_lock(
            DistributedLockLeaseId(cast(str, args.lease_id)),
            owner_id=DistributedLockOwnerId(cast(str, args.owner_id)),
        )
        if lock_lifecycle is None:
            raise ValueError("distributed runtime coordinator is not configured")
        _write_json(out, distributed_lock_lifecycle_body(lock_lifecycle))
        return
    raise ValueError(f"unknown distributed command: {distributed_command}")
