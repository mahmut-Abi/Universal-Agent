from __future__ import annotations

import asyncio
from collections.abc import Sequence
from types import MappingProxyType

from universal_agent.agentd.http import HttpResponse
from universal_agent.agentd.json_values import _json_value
from universal_agent.agentd.routing import (
    _optional_bool_query,
    _optional_event_cursor,
    _optional_float_query,
    _optional_positive_int_query,
)
from universal_agent.agentd.session_representations import (
    event_body,
    session_body,
    session_summary_body,
)
from universal_agent.core import (
    ExecutionResult,
    JsonMapping,
    JsonValue,
    SessionId,
    dumps_json,
    immutable_json,
)
from universal_agent.distributed import (
    DistributedCancellationResult,
    DistributedHealthReport,
    DistributedLockLease,
    DistributedLockLifecycleResult,
    DistributedMaintenanceResult,
    DistributedPruneResult,
    DistributedRuntimeSnapshot,
    DistributedSchedulingResult,
    DistributedWorkerLifecycleResult,
    WorkerRecord,
    WorkerRunResult,
    WorkItem,
)
from universal_agent.runtime import (
    RuntimeEventBatch,
    RuntimeRun,
    RuntimeSessionBatch,
)
from universal_agent.service import (
    AuditRecordView,
    CapabilityView,
    DistributedPendingActionSchedulingResult,
    DoctorReportView,
    DomainPackageView,
    DomainView,
    EvaluatorView,
    HealthView,
    MemoryView,
    MultiAgentView,
    PolicyView,
    ProfileView,
    ReadyView,
    RuntimeConfigDomainView,
    RuntimeConfigView,
    RuntimeCostView,
    RuntimeLogRecordView,
    RuntimeMetricsView,
    RuntimeModelConfigView,
    RuntimeSecretRefView,
    RuntimeService,
    RuntimeTraceSpanView,
    StateEventRepairReport,
    ToolView,
)


def runtime_run_body(run: RuntimeRun) -> JsonMapping:
    return immutable_json(
        {
            "result": execution_result_body(run.result),
            "session": dict(session_body(run.session)),
        }
    )


def event_batch_body(batch: RuntimeEventBatch) -> JsonMapping:
    return immutable_json(
        {
            "events": [event_body(item) for item in batch.events],
            "next_cursor": batch.next_cursor,
        }
    )


def session_batch_body(batch: RuntimeSessionBatch) -> JsonMapping:
    return immutable_json(
        {
            "sessions": [session_summary_body(item) for item in batch.sessions],
            "next_cursor": batch.next_cursor,
        }
    )


async def _stream_events_for_sse(
    service: RuntimeService,
    session_id: SessionId,
    path: str,
) -> RuntimeEventBatch:
    after_event_id = _optional_event_cursor(path)
    limit = _optional_positive_int_query(path, "limit")
    if not (_optional_bool_query(path, "wait") or False):
        return await service.stream_events(session_id, after_event_id=after_event_id, limit=limit)

    timeout_seconds = _optional_float_query(
        path,
        "timeout_seconds",
        default=10.0,
        minimum=0.0,
        maximum=30.0,
    )
    poll_interval_seconds = _optional_float_query(
        path,
        "poll_interval_seconds",
        default=0.25,
        minimum=0.001,
        maximum=5.0,
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    batch = await service.stream_events(session_id, after_event_id=after_event_id, limit=limit)
    while not batch.events and loop.time() < deadline:
        await asyncio.sleep(min(poll_interval_seconds, max(0.0, deadline - loop.time())))
        batch = await service.stream_events(session_id, after_event_id=after_event_id, limit=limit)
    return batch


def sse_event_batch_response(batch: RuntimeEventBatch) -> HttpResponse:
    return HttpResponse(
        status_code=200,
        body=event_batch_body(batch),
        headers=MappingProxyType(
            {
                "content-type": "text/event-stream",
                "cache-control": "no-cache",
            }
        ),
        text_body=sse_event_batch_text(batch),
    )


def sse_event_batch_text(batch: RuntimeEventBatch) -> str:
    chunks: list[str] = []
    for event in batch.events:
        chunks.append(f"id: {event.event_id}\n")
        chunks.append(f"event: {event.type}\n")
        chunks.append("data: ")
        chunks.append(dumps_json(event_body(event)))
        chunks.append("\n\n")
    if not batch.events:
        chunks.append(": heartbeat\n\n")
    if batch.next_cursor is not None:
        chunks.append(f": next_cursor={batch.next_cursor}\n\n")
    return "".join(chunks)


def execution_result_body(result: ExecutionResult) -> dict[str, JsonValue]:
    return {
        "status": result.status.value,
        "session_id": str(result.session_id),
        "goal_id": str(result.goal_id),
        "task_id": str(result.task_id),
        "iterations": result.iterations,
        "reason": result.reason,
        "error_code": result.error_code.value if result.error_code is not None else None,
        "user_message": result.user_message,
    }


def health_body(view: HealthView) -> JsonMapping:
    return immutable_json({"status": view.status, "service": view.service})


def ready_body(view: ReadyView) -> JsonMapping:
    return immutable_json(
        {
            "ready": view.ready,
            "reason": view.reason,
            "domain_count": view.domain_count,
            "capability_count": view.capability_count,
            "tool_count": view.tool_count,
        }
    )


def config_body(view: RuntimeConfigView) -> JsonMapping:
    return immutable_json(
        {
            "environment": _json_value(view.environment),
            "model": _runtime_model_config_body(view.model),
            "store": {
                "backend": view.store_backend,
                "path": view.store_path,
            },
            "state_event_commit": {
                "supported": view.state_event_commit_supported,
                "strategy": view.state_event_commit_strategy,
                "shared_store": view.state_event_commit_shared_store,
            },
            "distributed_queue": {
                "backend": view.distributed_queue_backend,
                "path": view.distributed_queue_path,
            },
            "distributed_locks": {
                "backend": view.distributed_locks_backend,
                "path": view.distributed_locks_path,
            },
            "distributed_workers": {
                "backend": view.distributed_workers_backend,
                "path": view.distributed_workers_path,
            },
            "distributed_terminal_retention_seconds": (view.distributed_terminal_retention_seconds),
            "limits": {
                "max_iterations": view.max_iterations,
                "max_recovery_steps": view.max_recovery_steps,
            },
            "domains": [_runtime_config_domain_body(domain) for domain in view.domains],
            "secrets": [_runtime_secret_ref_body(secret) for secret in view.secrets],
        }
    )


def _runtime_secret_ref_body(view: RuntimeSecretRefView) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "name": view.name,
        "source": view.source,
        "key": view.key,
        "required": view.required,
    }
    if view.available is not None:
        body["available"] = view.available
    if view.status is not None:
        body["status"] = view.status
    return body


def _runtime_model_config_body(view: RuntimeModelConfigView) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "provider": view.provider,
        "name": view.name,
        "timeout_seconds": view.timeout_seconds,
    }
    if view.endpoint is not None:
        body["endpoint"] = view.endpoint
    if view.api_key_secret is not None:
        body["api_key_secret"] = view.api_key_secret
    if view.headers:
        body["headers"] = _json_value(view.headers)
    if view.response_format is not None:
        body["response_format"] = view.response_format
    return body


def _runtime_config_domain_body(view: RuntimeConfigDomainView) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "name": view.name,
        "version": view.version,
        "primary": view.primary,
    }
    if view.backend is not None:
        body["backend"] = view.backend
    if view.settings:
        body["settings"] = _json_value(view.settings)
    return body


def metrics_body(view: RuntimeMetricsView) -> JsonMapping:
    return immutable_json(
        {
            "session_count": view.session_count,
            "active_session_count": view.active_session_count,
            "waiting_session_count": view.waiting_session_count,
            "completed_goal_count": view.completed_goal_count,
            "failed_goal_count": view.failed_goal_count,
            "cancelled_goal_count": view.cancelled_goal_count,
            "event_count": view.event_count,
            "action_started_count": view.action_started_count,
            "action_completed_count": view.action_completed_count,
            "decision_generated_count": view.decision_generated_count,
            "decision_validated_count": view.decision_validated_count,
            "decision_rejected_count": view.decision_rejected_count,
            "tool_failure_count": view.tool_failure_count,
            "policy_denial_count": view.policy_denial_count,
            "confirmation_required_count": view.confirmation_required_count,
            "recovery_planned_count": view.recovery_planned_count,
            "recovery_exhausted_count": view.recovery_exhausted_count,
            "human_intervention_count": view.human_intervention_count,
            "resource_lock_acquired_count": view.resource_lock_acquired_count,
            "resource_lock_released_count": view.resource_lock_released_count,
            "resource_conflict_count": view.resource_conflict_count,
            "active_resource_lock_count": view.active_resource_lock_count,
            "model_call_count": view.model_call_count,
            "model_input_token_count": view.model_input_token_count,
            "model_output_token_count": view.model_output_token_count,
            "model_total_token_count": view.model_total_token_count,
            "model_estimated_cost_micros": view.model_estimated_cost_micros,
        }
    )


def distributed_maintenance_body(view: DistributedMaintenanceResult) -> JsonMapping:
    return immutable_json(
        {
            "ran_at": view.ran_at.isoformat(),
            "expired_work_items": [
                distributed_work_item_summary_body(item) for item in view.expired_work_items
            ],
            "expired_locks": [
                {
                    "lock_key": lock.lock_key,
                    "owner_id": str(lock.owner_id),
                    "lease_id": str(lock.lease_id),
                }
                for lock in view.expired_locks
            ],
            "expired_workers": [
                {
                    "worker_id": str(worker.worker_id),
                    "status": worker.status.value,
                    "last_error": worker.last_error,
                }
                for worker in view.expired_workers
            ],
            "snapshot": dict(distributed_snapshot_body(view.snapshot)),
            "health": dict(distributed_health_body(view.health)),
        }
    )


def distributed_prune_body(view: DistributedPruneResult) -> JsonMapping:
    return immutable_json(
        {
            "ran_at": view.ran_at.isoformat(),
            "before": None if view.before is None else view.before.isoformat(),
            "pruned_count": len(view.pruned_work_items),
            "pruned_work_items": [
                distributed_work_item_summary_body(item) for item in view.pruned_work_items
            ],
            "snapshot": dict(distributed_snapshot_body(view.snapshot)),
            "health": dict(distributed_health_body(view.health)),
        }
    )


def distributed_lock_lifecycle_body(view: DistributedLockLifecycleResult) -> JsonMapping:
    return immutable_json(
        {
            "lock": distributed_lock_lease_summary_body(view.lock),
            "snapshot": dict(distributed_snapshot_body(view.snapshot)),
            "health": dict(distributed_health_body(view.health)),
        }
    )


def distributed_lock_lease_summary_body(lock: DistributedLockLease) -> dict[str, JsonValue]:
    return {
        "lock_key": lock.lock_key,
        "owner_id": str(lock.owner_id),
        "lease_id": str(lock.lease_id),
        "acquired_at": lock.acquired_at.isoformat(),
        "heartbeat_at": lock.heartbeat_at.isoformat(),
        "lease_expires_at": lock.lease_expires_at.isoformat(),
        "metadata": _json_value(lock.metadata),
    }


def distributed_worker_lifecycle_body(view: DistributedWorkerLifecycleResult) -> JsonMapping:
    return immutable_json(
        {
            "worker": distributed_worker_record_summary_body(view.worker),
            "snapshot": dict(distributed_snapshot_body(view.snapshot)),
            "health": dict(distributed_health_body(view.health)),
        }
    )


def distributed_worker_run_body(view: WorkerRunResult) -> JsonMapping:
    return immutable_json(
        {
            "status": view.status.value,
            "worker_id": str(view.worker_id),
            "lease_id": None if view.lease_id is None else str(view.lease_id),
            "reason": view.reason,
            "work_item": None
            if view.work_item is None
            else distributed_work_item_summary_body(view.work_item),
        }
    )


def distributed_worker_run_batch_body(views: Sequence[WorkerRunResult]) -> JsonMapping:
    return immutable_json(
        {
            "results": [dict(distributed_worker_run_body(view)) for view in views],
            "processed_count": sum(1 for view in views if view.work_item is not None),
            "terminal_status": None if not views else views[-1].status.value,
        }
    )


def distributed_worker_record_summary_body(worker: WorkerRecord) -> dict[str, JsonValue]:
    return {
        "worker_id": str(worker.worker_id),
        "status": worker.status.value,
        "registered_at": worker.registered_at.isoformat(),
        "heartbeat_at": worker.heartbeat_at.isoformat(),
        "lease_expires_at": worker.lease_expires_at.isoformat(),
        "capabilities": list(worker.capabilities),
        "metadata": _json_value(worker.metadata),
        "last_error": worker.last_error,
    }


def distributed_scheduling_body(view: DistributedSchedulingResult) -> JsonMapping:
    return immutable_json(
        {
            "scheduled_work_item": distributed_work_item_summary_body(view.scheduled_work_item),
            "snapshot": dict(distributed_snapshot_body(view.snapshot)),
            "health": dict(distributed_health_body(view.health)),
        }
    )


def distributed_pending_action_scheduling_body(
    view: DistributedPendingActionSchedulingResult,
) -> JsonMapping:
    return immutable_json(
        {
            "scheduled_count": len(view.scheduled_work_items),
            "scheduled_work_items": [
                distributed_work_item_summary_body(item) for item in view.scheduled_work_items
            ],
            "snapshot": dict(distributed_snapshot_body(view.snapshot)),
            "health": dict(distributed_health_body(view.health)),
        }
    )


def distributed_cancellation_body(view: DistributedCancellationResult) -> JsonMapping:
    return immutable_json(
        {
            "cancelled_work_item": distributed_work_item_summary_body(view.cancelled_work_item),
            "snapshot": dict(distributed_snapshot_body(view.snapshot)),
            "health": dict(distributed_health_body(view.health)),
        }
    )


def distributed_work_item_summary_body(item: WorkItem) -> dict[str, JsonValue]:
    return {
        "work_item_id": str(item.work_item_id),
        "kind": item.kind,
        "status": item.status.value,
        "session_id": None if item.session_id is None else str(item.session_id),
        "task_id": None if item.task_id is None else str(item.task_id),
        "action_id": None if item.action_id is None else str(item.action_id),
        "priority": item.priority,
        "attempts": item.attempts,
        "max_attempts": item.max_attempts,
        "last_error": item.last_error,
    }


def distributed_snapshot_body(view: DistributedRuntimeSnapshot) -> JsonMapping:
    return immutable_json(
        {
            "work_queue": {
                "total_count": view.work_queue.total_count,
                "queued_count": view.work_queue.queued_count,
                "leased_count": view.work_queue.leased_count,
                "completed_count": view.work_queue.completed_count,
                "failed_count": view.work_queue.failed_count,
                "cancelled_count": view.work_queue.cancelled_count,
                "items": [
                    {
                        "work_item_id": str(item.work_item_id),
                        "kind": item.kind,
                        "status": item.status.value,
                        "session_id": None if item.session_id is None else str(item.session_id),
                        "task_id": None if item.task_id is None else str(item.task_id),
                        "action_id": None if item.action_id is None else str(item.action_id),
                        "priority": item.priority,
                        "attempts": item.attempts,
                        "max_attempts": item.max_attempts,
                        "available_at": item.available_at.isoformat(),
                        "worker_id": None if item.worker_id is None else str(item.worker_id),
                        "lease_expires_at": None
                        if item.lease_expires_at is None
                        else item.lease_expires_at.isoformat(),
                        "last_error": item.last_error,
                    }
                    for item in view.work_queue.items
                ],
            },
            "locks": [
                {
                    "lock_key": lock.lock_key,
                    "owner_id": str(lock.owner_id),
                    "lease_id": str(lock.lease_id),
                    "acquired_at": lock.acquired_at.isoformat(),
                    "heartbeat_at": lock.heartbeat_at.isoformat(),
                    "lease_expires_at": lock.lease_expires_at.isoformat(),
                    "metadata": _json_value(lock.metadata),
                }
                for lock in view.locks
            ],
            "workers": {
                "total_count": view.workers.total_count,
                "online_count": view.workers.online_count,
                "draining_count": view.workers.draining_count,
                "offline_count": view.workers.offline_count,
                "lost_count": view.workers.lost_count,
                "workers": [
                    {
                        "worker_id": str(worker.worker_id),
                        "status": worker.status.value,
                        "registered_at": worker.registered_at.isoformat(),
                        "heartbeat_at": worker.heartbeat_at.isoformat(),
                        "lease_expires_at": worker.lease_expires_at.isoformat(),
                        "capabilities": list(worker.capabilities),
                        "metadata": _json_value(worker.metadata),
                        "last_error": worker.last_error,
                    }
                    for worker in view.workers.workers
                ],
            },
        }
    )


def distributed_health_body(view: DistributedHealthReport) -> JsonMapping:
    return immutable_json(
        {
            "status": view.status.value,
            "checks": [
                {
                    "name": check.name,
                    "status": check.status.value,
                    "message": check.message,
                }
                for check in view.checks
            ],
            "capacity_gaps": [
                {
                    "kind": gap.kind,
                    "queued_count": gap.queued_count,
                    "capable_online_workers": gap.capable_online_workers,
                }
                for gap in view.capacity_gaps
            ],
            "expiring_leases": [
                {
                    "lease_type": lease.lease_type,
                    "key": lease.key,
                    "owner_id": lease.owner_id,
                    "lease_expires_at": lease.lease_expires_at.isoformat(),
                    "seconds_remaining": lease.seconds_remaining,
                }
                for lease in view.expiring_leases
            ],
            "recommendations": [
                {
                    "code": recommendation.code,
                    "severity": recommendation.severity.value,
                    "target": recommendation.target,
                    "message": recommendation.message,
                }
                for recommendation in view.recommendations
            ],
        }
    )


def cost_body(view: RuntimeCostView) -> JsonMapping:
    return immutable_json(
        {
            "model_call_count": view.model_call_count,
            "input_tokens": view.input_tokens,
            "output_tokens": view.output_tokens,
            "total_tokens": view.total_tokens,
            "estimated_cost_micros": view.estimated_cost_micros,
            "currency": view.currency,
            "by_model": [
                {
                    "provider": item.provider,
                    "model": item.model,
                    "call_count": item.call_count,
                    "input_tokens": item.input_tokens,
                    "output_tokens": item.output_tokens,
                    "total_tokens": item.total_tokens,
                    "estimated_cost_micros": item.estimated_cost_micros,
                    "currency": item.currency,
                }
                for item in view.by_model
            ],
        }
    )


def doctor_body(view: DoctorReportView) -> JsonMapping:
    return immutable_json(
        {
            "status": view.status,
            "checks": [
                {
                    "name": check.name,
                    "status": check.status,
                    "message": check.message,
                }
                for check in view.checks
            ],
        }
    )


def state_event_repair_body(view: StateEventRepairReport) -> JsonMapping:
    return immutable_json(
        {
            "status": view.status,
            "repaired_event_count": view.repaired_event_count,
            "skipped_item_count": view.skipped_item_count,
            "repairs": [
                {
                    "event": event_body(repair.event),
                    "reason": repair.reason,
                }
                for repair in view.repairs
            ],
            "skipped": [
                {
                    "session_id": str(skip.session_id),
                    "event_id": skip.event_id,
                    "reason": skip.reason,
                }
                for skip in view.skipped
            ],
        }
    )


def audit_records_body(records: tuple[AuditRecordView, ...]) -> JsonMapping:
    return immutable_json({"audit_records": [audit_record_body(record) for record in records]})


def log_records_body(records: tuple[RuntimeLogRecordView, ...]) -> JsonMapping:
    return immutable_json({"logs": [log_record_body(record) for record in records]})


def trace_spans_body(spans: tuple[RuntimeTraceSpanView, ...]) -> JsonMapping:
    return immutable_json({"spans": [trace_span_body(span) for span in spans]})


def trace_span_body(view: RuntimeTraceSpanView) -> dict[str, JsonValue]:
    return {
        "trace_id": view.trace_id,
        "span_id": view.span_id,
        "parent_span_id": view.parent_span_id,
        "name": view.name,
        "kind": view.kind,
        "status": view.status,
        "session_id": str(view.session_id),
        "goal_id": str(view.goal_id),
        "task_id": str(view.task_id),
        "action_id": str(view.action_id) if view.action_id is not None else None,
        "start_time": view.start_time.isoformat(),
        "end_time": view.end_time.isoformat(),
        "duration_ms": view.duration_ms,
        "attributes": _json_value(view.attributes),
    }


def log_record_body(view: RuntimeLogRecordView) -> dict[str, JsonValue]:
    return {
        "log_id": view.log_id,
        "level": view.level,
        "message": view.message,
        "event_type": view.event_type,
        "session_id": str(view.session_id),
        "goal_id": str(view.goal_id),
        "task_id": str(view.task_id),
        "action_id": str(view.action_id) if view.action_id is not None else None,
        "data": _json_value(view.data),
        "occurred_at": view.occurred_at.isoformat(),
    }


def audit_record_body(view: AuditRecordView) -> dict[str, JsonValue]:
    return {
        "record_id": view.record_id,
        "session_id": str(view.session_id),
        "goal_id": str(view.goal_id),
        "task_id": str(view.task_id),
        "action_id": str(view.action_id) if view.action_id is not None else None,
        "capability": view.capability,
        "tool_name": view.tool_name,
        "side_effect": view.side_effect,
        "risk": view.risk,
        "policy_effect": view.policy_effect,
        "policy_name": view.policy_name,
        "status": view.status,
        "occurred_at": view.occurred_at.isoformat(),
        "completed_at": None if view.completed_at is None else view.completed_at.isoformat(),
        "error_code": view.error_code.value if view.error_code is not None else None,
    }


def domain_body(view: DomainView) -> dict[str, JsonValue]:
    return {
        "name": view.name,
        "version": view.version,
        "description": view.description,
        "primary": view.primary,
        "ontology": list(view.ontology),
        "capability_names": list(view.capability_names),
        "evaluator_names": list(view.evaluator_names),
    }


def domain_package_body(view: DomainPackageView) -> dict[str, JsonValue]:
    return {
        "name": view.name,
        "version": view.version,
        "description": view.description,
        "author": view.author,
        "entrypoint": view.entrypoint,
        "tags": list(view.tags),
        "ontology": list(view.ontology),
        "capability_names": list(view.capability_names),
        "tool_names": list(view.tool_names),
        "policy_names": list(view.policy_names),
        "procedure_names": list(view.procedure_names),
        "knowledge_names": list(view.knowledge_names),
        "evaluator_names": list(view.evaluator_names),
        "context_provider_names": list(view.context_provider_names),
        "prompt_names": list(view.prompt_names),
        "resource_names": list(view.resource_names),
        "dependencies": [
            {"name": dependency.name, "version": dependency.version}
            for dependency in view.dependencies
        ],
        "required_tools": list(view.required_tools),
        "compatibility": {
            "runtime_api": view.runtime_api_compatibility,
            "domain_api": view.domain_api_compatibility,
        },
        "security": dict(view.security),
        "root_path": view.root_path,
        "manifest_path": view.manifest_path,
    }


def capability_body(view: CapabilityView) -> dict[str, JsonValue]:
    return {
        "name": view.name,
        "description": view.description,
        "category": view.category.value,
        "risk": view.risk.value,
        "domain_name": view.domain_name,
        "domain_version": view.domain_version,
        "tool_names": list(view.tool_names),
        "required_arguments": list(view.required_arguments),
        "argument_schema": dict(view.argument_schema),
    }


def tool_body(view: ToolView) -> dict[str, JsonValue]:
    return {
        "name": view.name,
        "description": view.description,
        "capabilities": list(view.capabilities),
        "required_arguments": list(view.required_arguments),
        "argument_schema": dict(view.argument_schema),
        "side_effect": view.side_effect.value,
        "risk": view.risk.value,
        "timeout_seconds": view.timeout_seconds,
        "priority": view.priority,
        "domain_name": view.domain_name,
        "domain_version": view.domain_version,
    }


def policy_body(view: PolicyView) -> dict[str, JsonValue]:
    return {
        "name": view.name,
        "description": view.description,
        "policy_type": view.policy_type,
        "effect": None if view.effect is None else view.effect.value,
        "capability_names": list(view.capability_names),
        "categories": [item.value for item in view.categories],
        "risks": [item.value for item in view.risks],
        "domain_name": view.domain_name,
        "domain_version": view.domain_version,
    }


def evaluator_body(view: EvaluatorView) -> dict[str, JsonValue]:
    return {
        "name": view.name,
        "evaluator_type": view.evaluator_type,
        "domain_name": view.domain_name,
        "domain_version": view.domain_version,
    }


def memory_body(view: MemoryView) -> dict[str, JsonValue]:
    return {
        "memory_id": view.memory_id,
        "kind": view.kind.value,
        "subject": view.subject,
        "content": view.content,
        "scope": view.scope,
        "confidence": view.confidence,
        "source_session_id": None
        if view.source_session_id is None
        else str(view.source_session_id),
        "created_at": view.created_at.isoformat(),
    }


def profile_body(view: ProfileView) -> dict[str, JsonValue]:
    return {
        "name": view.name,
        "version": view.version,
        "description": view.description,
        "domain_name": view.domain_name,
        "domain_version": view.domain_version,
        "domains": [
            {"name": identity.name, "version": identity.version} for identity in view.domains
        ],
    }


def multi_agent_body(view: MultiAgentView) -> JsonMapping:
    return immutable_json(
        {
            "enabled": view.enabled,
            "profile_count": view.profile_count,
            "instance_count": view.instance_count,
            "ready_instance_count": view.ready_instance_count,
            "busy_instance_count": view.busy_instance_count,
            "draining_instance_count": view.draining_instance_count,
            "offline_instance_count": view.offline_instance_count,
            "delegation_task_count": view.delegation_task_count,
            "profiles": [
                {
                    "name": profile.name,
                    "version": profile.version,
                    "domains": [
                        {"name": identity.name, "version": identity.version}
                        for identity in profile.domains
                    ],
                    "permissions": list(profile.permissions),
                    "capabilities": list(profile.capabilities),
                    "description": profile.description,
                }
                for profile in view.profiles
            ],
            "instances": [
                {
                    "agent_id": instance.agent_id,
                    "profile_name": instance.profile_name,
                    "profile_version": instance.profile_version,
                    "status": instance.status.value,
                    "session_id": None if instance.session_id is None else str(instance.session_id),
                    "endpoint": instance.endpoint,
                }
                for instance in view.instances
            ],
            "delegation_tasks": [
                {
                    "task_id": task.task_id,
                    "child_count": task.child_count,
                    "delegation_depth": task.delegation_depth,
                }
                for task in view.delegation_tasks
            ],
        }
    )
