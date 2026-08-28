from __future__ import annotations

from collections.abc import Sequence
from types import MappingProxyType

from universal_agent.agentd.http import HttpResponse
from universal_agent.agentd.json_values import _json_value, _object_body
from universal_agent.agentd.routing import (
    _optional_bool_query,
    _optional_event_cursor,
    _optional_float_query,
    _optional_positive_int_query,
)
from universal_agent.agentd.session_representations import event_body
from universal_agent.core import (
    ExecutionResult,
    JsonMapping,
    JsonValue,
    SessionId,
    dumps_json,
    immutable_json,
)
from universal_agent.core.polling import poll_async_result
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
    return immutable_json(_object_body(run))


def event_batch_body(batch: RuntimeEventBatch) -> JsonMapping:
    return immutable_json(_object_body(batch))


def session_batch_body(batch: RuntimeSessionBatch) -> JsonMapping:
    return immutable_json(_object_body(batch))


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
    return await poll_async_result(
        lambda: service.stream_events(session_id, after_event_id=after_event_id, limit=limit),
        retry_if=lambda batch: not batch.events,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


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
    return _object_body(result)


def health_body(view: HealthView) -> JsonMapping:
    return immutable_json(_object_body(view))


def ready_body(view: ReadyView) -> JsonMapping:
    return immutable_json(_object_body(view))


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
    return immutable_json(_object_body(view))


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
    return _object_body(lock)


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
    return _object_body(worker)


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
    return immutable_json(_object_body(view))


def distributed_health_body(view: DistributedHealthReport) -> JsonMapping:
    return immutable_json(_object_body(view))


def cost_body(view: RuntimeCostView) -> JsonMapping:
    return immutable_json(_object_body(view))


def doctor_body(view: DoctorReportView) -> JsonMapping:
    return immutable_json(_object_body(view))


def state_event_repair_body(view: StateEventRepairReport) -> JsonMapping:
    body = _object_body(view)
    body["repaired_event_count"] = view.repaired_event_count
    body["skipped_item_count"] = view.skipped_item_count
    return immutable_json(body)


def audit_records_body(records: tuple[AuditRecordView, ...]) -> JsonMapping:
    return immutable_json({"audit_records": [audit_record_body(record) for record in records]})


def log_records_body(records: tuple[RuntimeLogRecordView, ...]) -> JsonMapping:
    return immutable_json({"logs": [log_record_body(record) for record in records]})


def trace_spans_body(spans: tuple[RuntimeTraceSpanView, ...]) -> JsonMapping:
    return immutable_json({"spans": [trace_span_body(span) for span in spans]})


def trace_span_body(view: RuntimeTraceSpanView) -> dict[str, JsonValue]:
    return _object_body(view)


def log_record_body(view: RuntimeLogRecordView) -> dict[str, JsonValue]:
    return _object_body(view)


def audit_record_body(view: AuditRecordView) -> dict[str, JsonValue]:
    return _object_body(view)


def domain_body(view: DomainView) -> dict[str, JsonValue]:
    return _object_body(view)


def domain_package_body(view: DomainPackageView) -> dict[str, JsonValue]:
    body = _object_body(view)
    runtime_api = body.pop("runtime_api_compatibility")
    domain_api = body.pop("domain_api_compatibility")
    body["compatibility"] = {"runtime_api": runtime_api, "domain_api": domain_api}
    return body


def capability_body(view: CapabilityView) -> dict[str, JsonValue]:
    return _object_body(view)


def tool_body(view: ToolView) -> dict[str, JsonValue]:
    return _object_body(view)


def policy_body(view: PolicyView) -> dict[str, JsonValue]:
    return _object_body(view)


def evaluator_body(view: EvaluatorView) -> dict[str, JsonValue]:
    return _object_body(view)


def memory_body(view: MemoryView) -> dict[str, JsonValue]:
    return _object_body(view)


def profile_body(view: ProfileView) -> dict[str, JsonValue]:
    return _object_body(view)


def multi_agent_body(view: MultiAgentView) -> JsonMapping:
    body = _object_body(view)
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
            "profiles": body["profiles"],
            "instances": body["instances"],
            "delegation_tasks": body["delegation_tasks"],
        }
    )
