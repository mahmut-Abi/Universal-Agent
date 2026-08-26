from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from universal_agent.agentd.console_routes import handle_console_route
from universal_agent.agentd.http import (
    AgentdAuthPolicy,
    HttpRequest,
    HttpResponse,
    _authenticate,
    _optional_datetime_field,
    bad_request,
    conflict,
    json_response,
    method_not_allowed,
    not_found,
    parse_goal_submission,
    text_response,
)
from universal_agent.agentd.representations import (
    _stream_events_for_sse,
    audit_records_body,
    capability_body,
    config_body,
    cost_body,
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
    doctor_body,
    domain_body,
    domain_package_body,
    evaluator_body,
    event_batch_body,
    health_body,
    log_records_body,
    memory_body,
    metrics_body,
    multi_agent_body,
    policy_body,
    profile_body,
    ready_body,
    runtime_run_body,
    session_batch_body,
    sse_event_batch_response,
    state_event_repair_body,
    tool_body,
    trace_spans_body,
)
from universal_agent.agentd.routing import (
    _distributed_cancel_route,
    _distributed_lock_lease_route,
    _distributed_lock_owner_id,
    _distributed_lock_ttl_seconds,
    _distributed_reason,
    _distributed_required_string,
    _distributed_schedule_action_route,
    _distributed_schedule_session_route,
    _distributed_schedule_task_route,
    _distributed_ttl_seconds,
    _distributed_worker_action_route,
    _distributed_worker_capabilities,
    _distributed_worker_run_max_items,
    _distributed_worker_run_optional_seconds,
    _distributed_worker_run_seconds,
    _domain_package_route,
    _normalize_path,
    _optional_event_cursor,
    _optional_positive_int_query,
    _optional_query_value,
    _optional_session_cursor,
    _profile_route,
    _session_route,
)
from universal_agent.agentd.session_representations import (
    session_body,
    session_evidence_body,
    session_explorer_body,
    session_world_body,
)
from universal_agent.core import JsonMapping, immutable_json
from universal_agent.distributed import (
    DistributedLockConflictError,
    DistributedLockLeaseLostError,
    WorkerNotFoundError,
    WorkItemNotFoundError,
)
from universal_agent.domain import AmbiguousDomainPackageError, DomainPackageNotFoundError
from universal_agent.profile import ProfileNotFoundError
from universal_agent.service import RuntimeService
from universal_agent.state import StateNotFoundError


class AgentdApp:
    """Framework-free route adapter for the future agentd process.

    It owns HTTP-shaped routing and JSON serialization. Runtime behavior stays
    behind RuntimeService, so a real socket server can later wrap this adapter
    without learning Kernel internals.
    """

    def __init__(
        self,
        service: RuntimeService,
        auth: AgentdAuthPolicy | None = None,
        *,
        evaluation_report_dir: str | Path | None = None,
    ) -> None:
        self._service = service
        self._auth = auth or AgentdAuthPolicy()
        self._evaluation_report_dir = (
            None if evaluation_report_dir is None else str(evaluation_report_dir)
        )

    async def handle(self, request: HttpRequest) -> HttpResponse:
        method = request.method.upper()
        path = _normalize_path(request.path)

        auth_response = _authenticate(self._auth, request, path, method=method)
        if auth_response is not None:
            return auth_response

        if path == "/health":
            return self._get(method, health_body(self._service.health()))
        if path == "/ready":
            return self._get(method, ready_body(self._service.ready()))
        console_response = await handle_console_route(
            self._service,
            self._evaluation_report_dir,
            request,
            method,
            path,
        )
        if console_response is not None:
            return console_response
        if path == "/v1/domains":
            return self._get(
                method,
                immutable_json(
                    {"domains": [domain_body(item) for item in self._service.domains()]}
                ),
            )
        if path == "/v1/domain-packages":
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                tag = _optional_query_value(request.path, "tag")
            except ValueError as exc:
                return bad_request(str(exc))
            return json_response(
                immutable_json(
                    {
                        "domain_packages": [
                            domain_package_body(item)
                            for item in self._service.domain_packages(tag=tag)
                        ]
                    }
                )
            )
        if path == "/v1/capabilities":
            return self._get(
                method,
                immutable_json(
                    {
                        "capabilities": [
                            capability_body(item) for item in self._service.capabilities()
                        ]
                    }
                ),
            )
        if path == "/v1/tools":
            return self._get(
                method,
                immutable_json({"tools": [tool_body(item) for item in self._service.tools()]}),
            )
        if path == "/v1/policies":
            return self._get(
                method,
                immutable_json(
                    {"policies": [policy_body(item) for item in self._service.policies()]}
                ),
            )
        if path == "/v1/evaluators":
            return self._get(
                method,
                immutable_json(
                    {"evaluators": [evaluator_body(item) for item in self._service.evaluators()]}
                ),
            )
        if path == "/v1/memory":
            return self._get(
                method,
                immutable_json(
                    {"memories": [memory_body(item) for item in self._service.memories()]}
                ),
            )
        if path == "/v1/profiles":
            return self._get(
                method,
                immutable_json(
                    {"profiles": [profile_body(item) for item in self._service.profiles()]}
                ),
            )
        if path == "/v1/multi-agent":
            return self._get(method, multi_agent_body(self._service.multi_agent()))
        if path == "/v1/config":
            return self._get(method, config_body(self._service.config()))
        profile_name = _profile_route(path)
        if profile_name is not None:
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                return json_response(profile_body(self._service.profile(profile_name)))
            except ProfileNotFoundError as exc:
                return not_found(str(exc))
        package_name, package_version = _domain_package_route(path)
        if package_name is not None:
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                return json_response(
                    domain_package_body(self._service.domain_package(package_name, package_version))
                )
            except DomainPackageNotFoundError as exc:
                return not_found(str(exc))
            except AmbiguousDomainPackageError as exc:
                return bad_request(str(exc))
        if path == "/v1/distributed/snapshot":
            if method != "GET":
                return method_not_allowed(("GET",))
            distributed_snapshot = self._service.distributed_snapshot()
            if distributed_snapshot is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_snapshot_body(distributed_snapshot))
        if path == "/v1/distributed/health":
            if method != "GET":
                return method_not_allowed(("GET",))
            health = self._service.distributed_health()
            if health is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_health_body(health))
        distributed_worker_id, distributed_worker_action = _distributed_worker_action_route(path)
        if distributed_worker_id is not None:
            if method != "POST":
                return method_not_allowed(("POST",))
            try:
                if distributed_worker_action == "register":
                    metadata = request.body.get("metadata")
                    if metadata is not None and not isinstance(metadata, Mapping):
                        return bad_request("distributed worker metadata must be an object")
                    lifecycle = self._service.distributed_register_worker(
                        distributed_worker_id,
                        capabilities=_distributed_worker_capabilities(request.body),
                        metadata=None if metadata is None else immutable_json(metadata),
                        ttl_seconds=_distributed_ttl_seconds(request.body),
                    )
                elif distributed_worker_action == "heartbeat":
                    lifecycle = self._service.distributed_heartbeat_worker(
                        distributed_worker_id,
                        ttl_seconds=_distributed_ttl_seconds(request.body),
                    )
                elif distributed_worker_action == "run-once":
                    worker_run = await self._service.distributed_run_worker_once(
                        distributed_worker_id,
                        lease_ttl_seconds=_distributed_worker_run_seconds(
                            request.body,
                            field_name="lease_ttl_seconds",
                            default=30.0,
                        ),
                        worker_ttl_seconds=_distributed_worker_run_seconds(
                            request.body,
                            field_name="worker_ttl_seconds",
                            default=30.0,
                        ),
                        heartbeat_interval_seconds=_distributed_worker_run_optional_seconds(
                            request.body,
                            field_name="heartbeat_interval_seconds",
                        ),
                    )
                    if worker_run is None:
                        return not_found("distributed runtime coordinator is not configured")
                    return json_response(distributed_worker_run_body(worker_run))
                elif distributed_worker_action == "run":
                    worker_runs = await self._service.distributed_run_worker_until_idle(
                        distributed_worker_id,
                        max_items=_distributed_worker_run_max_items(request.body),
                        lease_ttl_seconds=_distributed_worker_run_seconds(
                            request.body,
                            field_name="lease_ttl_seconds",
                            default=30.0,
                        ),
                        worker_ttl_seconds=_distributed_worker_run_seconds(
                            request.body,
                            field_name="worker_ttl_seconds",
                            default=30.0,
                        ),
                        heartbeat_interval_seconds=_distributed_worker_run_optional_seconds(
                            request.body,
                            field_name="heartbeat_interval_seconds",
                        ),
                    )
                    if worker_runs is None:
                        return not_found("distributed runtime coordinator is not configured")
                    return json_response(distributed_worker_run_batch_body(worker_runs))
                elif distributed_worker_action == "drain":
                    lifecycle = self._service.distributed_drain_worker(
                        distributed_worker_id,
                        reason=_distributed_reason(
                            request.body,
                            default="worker draining from agentd",
                            field_name="distributed worker drain reason",
                        ),
                    )
                elif distributed_worker_action == "offline":
                    lifecycle = self._service.distributed_mark_worker_offline(
                        distributed_worker_id,
                        reason=_distributed_reason(
                            request.body,
                            default="worker offline from agentd",
                            field_name="distributed worker offline reason",
                        ),
                    )
                else:
                    return not_found(f"unknown route: {path}")
            except WorkerNotFoundError as exc:
                return not_found(str(exc))
            except ValueError as exc:
                return bad_request(str(exc))
            if lifecycle is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_worker_lifecycle_body(lifecycle))
        if path == "/v1/distributed/locks/acquire":
            if method != "POST":
                return method_not_allowed(("POST",))
            metadata = request.body.get("metadata")
            if metadata is not None and not isinstance(metadata, Mapping):
                return bad_request("distributed lock metadata must be an object")
            try:
                lock_lifecycle = self._service.distributed_acquire_lock(
                    lock_key=_distributed_required_string(
                        request.body,
                        key="lock_key",
                        field_name="distributed lock key",
                    ),
                    owner_id=_distributed_lock_owner_id(request.body),
                    ttl_seconds=_distributed_lock_ttl_seconds(request.body),
                    metadata=None if metadata is None else immutable_json(metadata),
                )
            except DistributedLockConflictError as exc:
                return conflict(str(exc))
            except ValueError as exc:
                return bad_request(str(exc))
            if lock_lifecycle is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_lock_lifecycle_body(lock_lifecycle))
        distributed_lock_lease_id, distributed_lock_action = _distributed_lock_lease_route(path)
        if distributed_lock_lease_id is not None:
            if method != "POST":
                return method_not_allowed(("POST",))
            try:
                if distributed_lock_action == "heartbeat":
                    lock_lifecycle = self._service.distributed_heartbeat_lock(
                        distributed_lock_lease_id,
                        owner_id=_distributed_lock_owner_id(request.body),
                        ttl_seconds=_distributed_lock_ttl_seconds(request.body),
                    )
                elif distributed_lock_action == "release":
                    lock_lifecycle = self._service.distributed_release_lock(
                        distributed_lock_lease_id,
                        owner_id=_distributed_lock_owner_id(request.body),
                    )
                else:
                    return not_found(f"unknown route: {path}")
            except DistributedLockLeaseLostError as exc:
                return not_found(str(exc))
            except ValueError as exc:
                return bad_request(str(exc))
            if lock_lifecycle is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_lock_lifecycle_body(lock_lifecycle))
        if path == "/v1/distributed/goals":
            if method != "POST":
                return method_not_allowed(("POST",))
            priority = request.body.get("priority", 0)
            if not isinstance(priority, int):
                return bad_request("distributed schedule priority must be an integer")
            max_attempts = request.body.get("max_attempts", 3)
            if not isinstance(max_attempts, int):
                return bad_request("distributed schedule max_attempts must be an integer")
            try:
                submission = parse_goal_submission(request.body)
            except ValueError as exc:
                return bad_request(str(exc))
            if submission.profile_name is not None:
                profile_error = self._service.profile_selection_error(submission.profile_name)
                if profile_error is not None:
                    return bad_request(profile_error)
            try:
                scheduling = self._service.distributed_schedule_goal(
                    submission.goal,
                    submission.task,
                    priority=priority,
                    max_attempts=max_attempts,
                )
            except ValueError as exc:
                return bad_request(str(exc))
            if scheduling is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_scheduling_body(scheduling), status_code=202)
        if path == "/v1/distributed/pending-actions/schedule":
            if method != "POST":
                return method_not_allowed(("POST",))
            confirmed = request.body.get("confirmed")
            if not isinstance(confirmed, bool):
                return bad_request(
                    "distributed pending-action schedule confirmed must be a boolean"
                )
            if not confirmed:
                return bad_request("distributed pending-action schedule requires confirmed=true")
            priority = request.body.get("priority", 0)
            if not isinstance(priority, int):
                return bad_request("distributed schedule priority must be an integer")
            max_attempts = request.body.get("max_attempts", 3)
            if not isinstance(max_attempts, int):
                return bad_request("distributed schedule max_attempts must be an integer")
            try:
                pending_scheduling = await self._service.distributed_schedule_pending_actions(
                    confirmed=confirmed,
                    priority=priority,
                    max_attempts=max_attempts,
                )
            except ValueError as exc:
                return bad_request(str(exc))
            if pending_scheduling is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(
                distributed_pending_action_scheduling_body(pending_scheduling),
                status_code=202,
            )
        (
            distributed_schedule_action_session_id,
            distributed_schedule_action_task_id,
            distributed_schedule_action_id,
        ) = _distributed_schedule_action_route(path)
        if (
            distributed_schedule_action_session_id is not None
            and distributed_schedule_action_task_id is not None
            and distributed_schedule_action_id is not None
        ):
            if method != "POST":
                return method_not_allowed(("POST",))
            confirmed = request.body.get("confirmed")
            if not isinstance(confirmed, bool):
                return bad_request("distributed schedule-action confirmed must be a boolean")
            if not confirmed:
                return bad_request("distributed schedule-action requires confirmed=true")
            priority = request.body.get("priority", 0)
            if not isinstance(priority, int):
                return bad_request("distributed schedule priority must be an integer")
            max_attempts = request.body.get("max_attempts", 3)
            if not isinstance(max_attempts, int):
                return bad_request("distributed schedule max_attempts must be an integer")
            try:
                scheduling = self._service.distributed_schedule_action(
                    distributed_schedule_action_session_id,
                    distributed_schedule_action_task_id,
                    distributed_schedule_action_id,
                    confirmed=confirmed,
                    priority=priority,
                    max_attempts=max_attempts,
                )
            except ValueError as exc:
                return bad_request(str(exc))
            if scheduling is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_scheduling_body(scheduling))
        distributed_schedule_task_session_id, distributed_schedule_task_id = (
            _distributed_schedule_task_route(path)
        )
        if (
            distributed_schedule_task_session_id is not None
            and distributed_schedule_task_id is not None
        ):
            if method != "POST":
                return method_not_allowed(("POST",))
            payload = request.body.get("payload")
            if payload is not None and not isinstance(payload, Mapping):
                return bad_request("distributed schedule payload must be an object")
            priority = request.body.get("priority", 0)
            if not isinstance(priority, int):
                return bad_request("distributed schedule priority must be an integer")
            max_attempts = request.body.get("max_attempts", 3)
            if not isinstance(max_attempts, int):
                return bad_request("distributed schedule max_attempts must be an integer")
            try:
                scheduling = self._service.distributed_schedule_task(
                    distributed_schedule_task_session_id,
                    distributed_schedule_task_id,
                    payload=None if payload is None else immutable_json(payload),
                    priority=priority,
                    max_attempts=max_attempts,
                )
            except ValueError as exc:
                return bad_request(str(exc))
            if scheduling is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_scheduling_body(scheduling))
        distributed_schedule_session_id = _distributed_schedule_session_route(path)
        if distributed_schedule_session_id is not None:
            if method != "POST":
                return method_not_allowed(("POST",))
            payload = request.body.get("payload")
            if payload is not None and not isinstance(payload, Mapping):
                return bad_request("distributed schedule payload must be an object")
            priority = request.body.get("priority", 0)
            if not isinstance(priority, int):
                return bad_request("distributed schedule priority must be an integer")
            max_attempts = request.body.get("max_attempts", 3)
            if not isinstance(max_attempts, int):
                return bad_request("distributed schedule max_attempts must be an integer")
            try:
                scheduling = self._service.distributed_schedule_session(
                    distributed_schedule_session_id,
                    payload=None if payload is None else immutable_json(payload),
                    priority=priority,
                    max_attempts=max_attempts,
                )
            except ValueError as exc:
                return bad_request(str(exc))
            if scheduling is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_scheduling_body(scheduling))
        if path == "/v1/distributed/expire":
            if method != "POST":
                return method_not_allowed(("POST",))
            maintenance = self._service.distributed_expire()
            if maintenance is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_maintenance_body(maintenance))
        if path == "/v1/distributed/prune-terminal":
            if method != "POST":
                return method_not_allowed(("POST",))
            try:
                before = _optional_datetime_field(
                    request.body,
                    "before",
                    "distributed prune before",
                )
            except ValueError as exc:
                return bad_request(str(exc))
            pruned = self._service.distributed_prune_terminal_work_items(before=before)
            if pruned is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_prune_body(pruned))
        distributed_cancel_work_item_id = _distributed_cancel_route(path)
        if distributed_cancel_work_item_id is not None:
            if method != "POST":
                return method_not_allowed(("POST",))
            reason = request.body.get(
                "reason",
                "distributed work item cancelled from agentd",
            )
            if not isinstance(reason, str):
                return bad_request("distributed cancel reason must be a string")
            if not reason.strip():
                return bad_request("distributed cancel reason must not be empty")
            try:
                cancellation = self._service.distributed_cancel_work_item(
                    distributed_cancel_work_item_id,
                    reason=reason,
                )
            except WorkItemNotFoundError as exc:
                return not_found(str(exc))
            if cancellation is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_cancellation_body(cancellation))
        if path == "/v1/metrics":
            if method != "GET":
                return method_not_allowed(("GET",))
            return json_response(metrics_body(await self._service.metrics()))
        if path == "/v1/metrics/prometheus":
            if method != "GET":
                return method_not_allowed(("GET",))
            return text_response(
                await self._service.prometheus_metrics(),
                content_type="text/plain; version=0.0.4; charset=utf-8",
            )
        if path == "/v1/cost":
            if method != "GET":
                return method_not_allowed(("GET",))
            return json_response(cost_body(await self._service.cost()))
        if path == "/v1/logs":
            if method != "GET":
                return method_not_allowed(("GET",))
            return json_response(log_records_body(await self._service.logs()))
        if path == "/v1/traces":
            if method != "GET":
                return method_not_allowed(("GET",))
            return json_response(trace_spans_body(await self._service.traces()))
        if path == "/v1/traces/otlp":
            if method != "GET":
                return method_not_allowed(("GET",))
            return json_response(await self._service.opentelemetry_traces())
        if path == "/v1/doctor/state-events/repair":
            if method != "POST":
                return method_not_allowed(("POST",))
            confirmed = request.body.get("confirmed", False)
            if not isinstance(confirmed, bool):
                return bad_request("state/event repair confirmed must be a boolean")
            dry_run = request.body.get("dry_run", False)
            if not isinstance(dry_run, bool):
                return bad_request("state/event repair dry_run must be a boolean")
            try:
                report = await self._service.repair_state_event_consistency(
                    confirmed=confirmed,
                    dry_run=dry_run,
                )
            except ValueError as exc:
                return bad_request(str(exc))
            return json_response(state_event_repair_body(report))
        if path == "/v1/doctor":
            if method != "GET":
                return method_not_allowed(("GET",))
            return json_response(doctor_body(await self._service.doctor()))
        if path == "/v1/audit":
            if method != "GET":
                return method_not_allowed(("GET",))
            return json_response(audit_records_body(await self._service.audit_records()))
        if path == "/v1/sessions":
            if method == "GET":
                try:
                    return json_response(
                        session_batch_body(
                            await self._service.stream_sessions(
                                after_session_id=_optional_session_cursor(request.path),
                                limit=_optional_positive_int_query(request.path, "limit"),
                            )
                        )
                    )
                except ValueError as exc:
                    return bad_request(str(exc))
            if method != "POST":
                return method_not_allowed(("GET", "POST"))
            try:
                submission = parse_goal_submission(request.body)
            except ValueError as exc:
                return bad_request(str(exc))
            if submission.profile_name is not None:
                profile_error = self._service.profile_selection_error(submission.profile_name)
                if profile_error is not None:
                    return bad_request(profile_error)
            run = await self._service.run_goal(submission.goal, submission.task)
            return json_response(runtime_run_body(run), status_code=201)

        session_id, suffix = _session_route(path)
        if session_id is not None and suffix == "":
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                return json_response(session_body(await self._service.get_session(session_id)))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if session_id is not None and suffix == "diagnostics":
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                return json_response(
                    session_explorer_body(await self._service.session_explorer(session_id))
                )
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if session_id is not None and suffix == "evidence":
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                return json_response(
                    session_evidence_body(await self._service.session_explorer(session_id))
                )
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if session_id is not None and suffix == "world":
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                return json_response(
                    session_world_body(
                        await self._service.session_world(
                            session_id,
                            entity_id=_optional_query_value(request.path, "entity_id"),
                            relation=_optional_query_value(request.path, "relation"),
                        )
                    )
                )
            except ValueError as exc:
                return bad_request(str(exc))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if session_id is not None and suffix == "events":
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                batch = await self._service.stream_events(
                    session_id,
                    after_event_id=_optional_event_cursor(request.path),
                    limit=_optional_positive_int_query(request.path, "limit"),
                )
                return json_response(event_batch_body(batch))
            except StateNotFoundError as exc:
                return not_found(str(exc))
            except ValueError as exc:
                return bad_request(str(exc))
        if session_id is not None and suffix == "events/stream":
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                batch = await _stream_events_for_sse(self._service, session_id, request.path)
                return sse_event_batch_response(batch)
            except StateNotFoundError as exc:
                return not_found(str(exc))
            except ValueError as exc:
                return bad_request(str(exc))
        if session_id is not None and suffix == "audit":
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                return json_response(
                    audit_records_body(await self._service.audit_records(session_id))
                )
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if session_id is not None and suffix == "cost":
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                return json_response(cost_body(await self._service.cost(session_id)))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if session_id is not None and suffix == "logs":
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                return json_response(log_records_body(await self._service.logs(session_id)))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if session_id is not None and suffix == "traces":
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                return json_response(trace_spans_body(await self._service.traces(session_id)))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if session_id is not None and suffix == "traces/otlp":
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                return json_response(await self._service.opentelemetry_traces(session_id))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if session_id is not None and suffix == "pause":
            if method != "POST":
                return method_not_allowed(("POST",))
            reason = request.body.get("reason", "session paused")
            if not isinstance(reason, str):
                return bad_request("pause reason must be a string")
            try:
                run = await self._service.pause_session(session_id, reason=reason)
                return json_response(runtime_run_body(run))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if session_id is not None and suffix == "resume":
            if method != "POST":
                return method_not_allowed(("POST",))
            confirmed = request.body.get("confirmed")
            if confirmed is not None and not isinstance(confirmed, bool):
                return bad_request("resume confirmed must be a boolean")
            try:
                session = await self._service.get_session(session_id)
                if session.pending_action is not None and confirmed is None:
                    return bad_request("resume requires boolean confirmed for pending action")
                run = await self._service.resume_session(session_id, confirmed=confirmed)
                return json_response(runtime_run_body(run))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if session_id is not None and suffix == "cancel":
            if method != "POST":
                return method_not_allowed(("POST",))
            reason = request.body.get("reason", "session cancelled")
            if not isinstance(reason, str):
                return bad_request("cancel reason must be a string")
            try:
                run = await self._service.cancel_session(session_id, reason=reason)
                return json_response(runtime_run_body(run))
            except StateNotFoundError as exc:
                return not_found(str(exc))

        return not_found(f"unknown route: {path}")

    def _get(self, method: str, body: JsonMapping) -> HttpResponse:
        if method != "GET":
            return method_not_allowed(("GET",))
        return json_response(body)
