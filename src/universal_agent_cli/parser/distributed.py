"""Distributed runtime command parser (advanced/experimental local primitives)."""

from __future__ import annotations

import argparse


def add_distributed_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    distributed = commands.add_parser(
        "distributed",
        help="(advanced/experimental) Inspect or drive local distributed runtime primitives.",
    )
    distributed_commands = distributed.add_subparsers(
        dest="distributed_command",
        required=True,
    )
    distributed_commands.add_parser("snapshot")
    distributed_commands.add_parser("health")
    distributed_commands.add_parser("expire")
    distributed_prune = distributed_commands.add_parser("prune-terminal")
    distributed_prune.add_argument("--before")
    distributed_schedule = distributed_commands.add_parser("schedule-session")
    distributed_schedule.add_argument("session_id")
    distributed_schedule.add_argument("--priority", type=int, default=0)
    distributed_schedule.add_argument("--max-attempts", type=int, default=3)
    distributed_schedule_goal = distributed_commands.add_parser("schedule-goal")
    distributed_schedule_goal.add_argument("profile")
    distributed_schedule_goal.add_argument("goal")
    distributed_schedule_goal.add_argument("--task", default="Run goal")
    distributed_schedule_goal.add_argument(
        "--success",
        action="append",
        default=[],
        help="Goal success criterion as KEY=JSON. Repeat for multiple criteria.",
    )
    distributed_schedule_goal.add_argument("--priority", type=int, default=0)
    distributed_schedule_goal.add_argument("--max-attempts", type=int, default=3)
    distributed_schedule_task = distributed_commands.add_parser("schedule-task")
    distributed_schedule_task.add_argument("session_id")
    distributed_schedule_task.add_argument("task_id")
    distributed_schedule_task.add_argument("--priority", type=int, default=0)
    distributed_schedule_task.add_argument("--max-attempts", type=int, default=3)
    distributed_schedule_action = distributed_commands.add_parser("schedule-action")
    distributed_schedule_action.add_argument("session_id")
    distributed_schedule_action.add_argument("task_id")
    distributed_schedule_action.add_argument("action_id")
    distributed_schedule_action.add_argument(
        "--confirmed", choices=("true", "false"), required=True
    )
    distributed_schedule_action.add_argument("--priority", type=int, default=0)
    distributed_schedule_action.add_argument("--max-attempts", type=int, default=3)
    distributed_pending_actions = distributed_commands.add_parser("schedule-pending-actions")
    distributed_pending_actions.add_argument(
        "--confirmed", choices=("true", "false"), required=True
    )
    distributed_pending_actions.add_argument("--priority", type=int, default=0)
    distributed_pending_actions.add_argument("--max-attempts", type=int, default=3)
    distributed_cancel = distributed_commands.add_parser("cancel")
    distributed_cancel.add_argument("work_item_id")
    distributed_cancel.add_argument(
        "--reason",
        default="distributed work item cancelled from CLI",
    )
    distributed_register = distributed_commands.add_parser("worker-register")
    distributed_register.add_argument("worker_id")
    distributed_register.add_argument("--capability", action="append", default=[])
    distributed_register.add_argument("--ttl-seconds", type=float, default=30.0)

    distributed_heartbeat = distributed_commands.add_parser("worker-heartbeat")
    distributed_heartbeat.add_argument("worker_id")
    distributed_heartbeat.add_argument("--ttl-seconds", type=float, default=30.0)

    distributed_worker_run = distributed_commands.add_parser("worker-run-once")
    distributed_worker_run.add_argument("worker_id")
    distributed_worker_run.add_argument("--lease-ttl-seconds", type=float, default=30.0)
    distributed_worker_run.add_argument("--worker-ttl-seconds", type=float, default=30.0)
    distributed_worker_run.add_argument("--heartbeat-interval-seconds", type=float)
    distributed_worker_run_batch = distributed_commands.add_parser("worker-run")
    distributed_worker_run_batch.add_argument("worker_id")
    distributed_worker_run_batch.add_argument("--max-items", type=int, default=1)
    distributed_worker_run_batch.add_argument("--lease-ttl-seconds", type=float, default=30.0)
    distributed_worker_run_batch.add_argument("--worker-ttl-seconds", type=float, default=30.0)
    distributed_worker_run_batch.add_argument("--heartbeat-interval-seconds", type=float)

    distributed_drain = distributed_commands.add_parser("worker-drain")
    distributed_drain.add_argument("worker_id")
    distributed_drain.add_argument("--reason", default="worker draining from CLI")

    distributed_offline = distributed_commands.add_parser("worker-offline")
    distributed_offline.add_argument("worker_id")
    distributed_offline.add_argument("--reason", default="worker offline from CLI")

    distributed_lock_acquire = distributed_commands.add_parser("lock-acquire")
    distributed_lock_acquire.add_argument("lock_key")
    distributed_lock_acquire.add_argument("--owner-id", required=True)
    distributed_lock_acquire.add_argument("--ttl-seconds", type=float, default=30.0)

    distributed_lock_heartbeat = distributed_commands.add_parser("lock-heartbeat")
    distributed_lock_heartbeat.add_argument("lease_id")
    distributed_lock_heartbeat.add_argument("--owner-id", required=True)
    distributed_lock_heartbeat.add_argument("--ttl-seconds", type=float, default=30.0)

    distributed_lock_release = distributed_commands.add_parser("lock-release")
    distributed_lock_release.add_argument("lease_id")
    distributed_lock_release.add_argument("--owner-id", required=True)
