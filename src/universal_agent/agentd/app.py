from __future__ import annotations

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
    _distributed_confirmed_schedule_payload,
    _distributed_lock_acquire_payload,
    _distributed_lock_lease_payload,
    _distributed_lock_lease_route,
    _distributed_reason_payload,
    _distributed_schedule_action_route,
    _distributed_schedule_payload,
    _distributed_schedule_session_route,
    _distributed_schedule_settings_payload,
    _distributed_schedule_task_route,
    _distributed_worker_action_route,
    _distributed_worker_registration_payload,
    _distributed_worker_run_batch_payload,
    _distributed_worker_run_payload,
    _distributed_worker_ttl_seconds,
    _domain_package_route,
    _normalize_path,
    _optional_event_cursor,
    _optional_positive_int_query,
    _optional_query_value,
    _optional_session_cursor,
    _profile_route,
    _session_reason_payload,
    _session_resume_payload,
    _session_route,
    _state_event_repair_payload,
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
                    registration_payload = _distributed_worker_registration_payload(request.body)
                    lifecycle = self._service.distributed_register_worker(
                        distributed_worker_id,
                        capabilities=registration_payload.capabilities,
                        metadata=registration_payload.metadata,
                        ttl_seconds=registration_payload.ttl_seconds,
                    )
                elif distributed_worker_action == "heartbeat":
                    ttl_seconds = _distributed_worker_ttl_seconds(request.body)
                    lifecycle = self._service.distributed_heartbeat_worker(
                        distributed_worker_id,
                        ttl_seconds=ttl_seconds,
                    )
                elif distributed_worker_action == "run-once":
                    worker_run_payload = _distributed_worker_run_payload(request.body)
                    worker_run = await self._service.distributed_run_worker_once(
                        distributed_worker_id,
                        lease_ttl_seconds=worker_run_payload.lease_ttl_seconds,
                        worker_ttl_seconds=worker_run_payload.worker_ttl_seconds,
                        heartbeat_interval_seconds=(
                            worker_run_payload.heartbeat_interval_seconds
                        ),
                    )
                    if worker_run is None:
                        return not_found("distributed runtime coordinator is not configured")
                    return json_response(distributed_worker_run_body(worker_run))
                elif distributed_worker_action == "run":
                    worker_batch_payload = _distributed_worker_run_batch_payload(request.body)
                    worker_runs = await self._service.distributed_run_worker_until_idle(
                        distributed_worker_id,
                        max_items=worker_batch_payload.max_items,
                        lease_ttl_seconds=worker_batch_payload.lease_ttl_seconds,
                        worker_ttl_seconds=worker_batch_payload.worker_ttl_seconds,
                        heartbeat_interval_seconds=(
                            worker_batch_payload.heartbeat_interval_seconds
                        ),
                    )
                    if worker_runs is None:
                        return not_found("distributed runtime coordinator is not configured")
                    return json_response(distributed_worker_run_batch_body(worker_runs))
                elif distributed_worker_action == "drain":
                    lifecycle = self._service.distributed_drain_worker(
                        distributed_worker_id,
                        reason=_distributed_reason_payload(
                            request.body,
                            default="worker draining from agentd",
                            field_name="distributed worker drain reason",
                        ),
                    )
                elif distributed_worker_action == "offline":
                    lifecycle = self._service.distributed_mark_worker_offline(
                        distributed_worker_id,
                        reason=_distributed_reason_payload(
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
            try:
                lock_payload = _distributed_lock_acquire_payload(request.body)
                lock_lifecycle = self._service.distributed_acquire_lock(
                    lock_key=lock_payload.lock_key,
                    owner_id=lock_payload.owner_id,
                    ttl_seconds=lock_payload.ttl_seconds,
                    metadata=lock_payload.metadata,
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
                lease_payload = _distributed_lock_lease_payload(request.body)
                if distributed_lock_action == "heartbeat":
                    lock_lifecycle = self._service.distributed_heartbeat_lock(
                        distributed_lock_lease_id,
                        owner_id=lease_payload.owner_id,
                        ttl_seconds=lease_payload.ttl_seconds,
                    )
                elif distributed_lock_action == "release":
                    lock_lifecycle = self._service.distributed_release_lock(
                        distributed_lock_lease_id,
                        owner_id=lease_payload.owner_id,
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
            try:
                schedule = _distributed_schedule_settings_payload(request.body)
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
                    priority=schedule.priority,
                    max_attempts=schedule.max_attempts,
                )
            except ValueError as exc:
                return bad_request(str(exc))
            if scheduling is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_scheduling_body(scheduling), status_code=202)
        if path == "/v1/distributed/pending-actions/schedule":
            if method != "POST":
                return method_not_allowed(("POST",))
            try:
                pending_action_schedule = _distributed_confirmed_schedule_payload(
                    request.body,
                    confirmed_field_name="distributed pending-action schedule confirmed",
                )
                if not pending_action_schedule.confirmed:
                    return bad_request(
                        "distributed pending-action schedule requires confirmed=true"
                    )
                pending_scheduling = await self._service.distributed_schedule_pending_actions(
                    confirmed=pending_action_schedule.confirmed,
                    priority=pending_action_schedule.priority,
                    max_attempts=pending_action_schedule.max_attempts,
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
            try:
                action_schedule = _distributed_confirmed_schedule_payload(
                    request.body,
                    confirmed_field_name="distributed schedule-action confirmed",
                )
                if not action_schedule.confirmed:
                    return bad_request("distributed schedule-action requires confirmed=true")
                scheduling = self._service.distributed_schedule_action(
                    distributed_schedule_action_session_id,
                    distributed_schedule_action_task_id,
                    distributed_schedule_action_id,
                    confirmed=action_schedule.confirmed,
                    priority=action_schedule.priority,
                    max_attempts=action_schedule.max_attempts,
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
            try:
                task_schedule = _distributed_schedule_payload(request.body)
                scheduling = self._service.distributed_schedule_task(
                    distributed_schedule_task_session_id,
                    distributed_schedule_task_id,
                    payload=task_schedule.payload,
                    priority=task_schedule.priority,
                    max_attempts=task_schedule.max_attempts,
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
            try:
                session_schedule = _distributed_schedule_payload(request.body)
                scheduling = self._service.distributed_schedule_session(
                    distributed_schedule_session_id,
                    payload=session_schedule.payload,
                    priority=session_schedule.priority,
                    max_attempts=session_schedule.max_attempts,
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
            try:
                cancel_reason = _distributed_reason_payload(
                    request.body,
                    default="distributed work item cancelled from agentd",
                    field_name="distributed cancel reason",
                )
                cancellation = self._service.distributed_cancel_work_item(
                    distributed_cancel_work_item_id,
                    reason=cancel_reason,
                )
            except WorkItemNotFoundError as exc:
                return not_found(str(exc))
            except ValueError as exc:
                return bad_request(str(exc))
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
            try:
                repair_payload = _state_event_repair_payload(request.body)
                report = await self._service.repair_state_event_consistency(
                    confirmed=repair_payload.confirmed,
                    dry_run=repair_payload.dry_run,
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
            try:
                pause_reason = _session_reason_payload(
                    request.body,
                    default="session paused",
                    field_name="pause reason",
                )
                run = await self._service.pause_session(session_id, reason=pause_reason)
                return json_response(runtime_run_body(run))
            except ValueError as exc:
                return bad_request(str(exc))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if session_id is not None and suffix == "resume":
            if method != "POST":
                return method_not_allowed(("POST",))
            try:
                resume_payload = _session_resume_payload(request.body)
                session = await self._service.get_session(session_id)
                if session.pending_action is not None and resume_payload.confirmed is None:
                    return bad_request("resume requires boolean confirmed for pending action")
                run = await self._service.resume_session(
                    session_id,
                    confirmed=resume_payload.confirmed,
                )
                return json_response(runtime_run_body(run))
            except ValueError as exc:
                return bad_request(str(exc))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if session_id is not None and suffix == "cancel":
            if method != "POST":
                return method_not_allowed(("POST",))
            try:
                session_cancel_reason = _session_reason_payload(
                    request.body,
                    default="session cancelled",
                    field_name="cancel reason",
                )
                run = await self._service.cancel_session(
                    session_id,
                    reason=session_cancel_reason,
                )
                return json_response(runtime_run_body(run))
            except ValueError as exc:
                return bad_request(str(exc))
            except StateNotFoundError as exc:
                return not_found(str(exc))

        return not_found(f"unknown route: {path}")

    def _get(self, method: str, body: JsonMapping) -> HttpResponse:
        if method != "GET":
            return method_not_allowed(("GET",))
        return json_response(body)
