from __future__ import annotations

from collections.abc import Callable
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
    AgentdRouteDefinition,
    AgentdRouteMatcher,
    _distributed_confirmed_schedule_payload,
    _distributed_lock_acquire_payload,
    _distributed_lock_lease_payload,
    _distributed_reason_payload,
    _distributed_schedule_payload,
    _distributed_schedule_settings_payload,
    _distributed_worker_registration_payload,
    _distributed_worker_run_batch_payload,
    _distributed_worker_run_payload,
    _distributed_worker_ttl_seconds,
    _normalize_path,
    _optional_event_cursor,
    _optional_positive_int_query,
    _optional_query_value,
    _optional_session_cursor,
    _session_reason_payload,
    _session_resume_payload,
    _state_event_repair_payload,
)
from universal_agent.agentd.session_representations import (
    session_body,
    session_evidence_body,
    session_explorer_body,
    session_world_body,
)
from universal_agent.core import ActionId, JsonMapping, SessionId, TaskId, immutable_json
from universal_agent.distributed import (
    DistributedLockConflictError,
    DistributedLockLeaseId,
    DistributedLockLeaseLostError,
    WorkerId,
    WorkerNotFoundError,
    WorkItemId,
    WorkItemNotFoundError,
)
from universal_agent.domain import AmbiguousDomainPackageError, DomainPackageNotFoundError
from universal_agent.profile import ProfileNotFoundError
from universal_agent.service import RuntimeService
from universal_agent.state import StateNotFoundError

_STATIC_GET_ROUTES = AgentdRouteMatcher(
    (
        AgentdRouteDefinition("health", "/health"),
        AgentdRouteDefinition("ready", "/ready"),
        AgentdRouteDefinition("domains", "/v1/domains"),
        AgentdRouteDefinition("domain_packages", "/v1/domain-packages"),
        AgentdRouteDefinition("capabilities", "/v1/capabilities"),
        AgentdRouteDefinition("tools", "/v1/tools"),
        AgentdRouteDefinition("policies", "/v1/policies"),
        AgentdRouteDefinition("evaluators", "/v1/evaluators"),
        AgentdRouteDefinition("memory", "/v1/memory"),
        AgentdRouteDefinition("profiles", "/v1/profiles"),
        AgentdRouteDefinition("multi_agent", "/v1/multi-agent"),
        AgentdRouteDefinition("config", "/v1/config"),
        AgentdRouteDefinition("distributed_snapshot", "/v1/distributed/snapshot"),
        AgentdRouteDefinition("distributed_health", "/v1/distributed/health"),
        AgentdRouteDefinition("metrics", "/v1/metrics"),
        AgentdRouteDefinition("metrics_prometheus", "/v1/metrics/prometheus"),
        AgentdRouteDefinition("cost", "/v1/cost"),
        AgentdRouteDefinition("logs", "/v1/logs"),
        AgentdRouteDefinition("traces", "/v1/traces"),
        AgentdRouteDefinition("traces_otlp", "/v1/traces/otlp"),
        AgentdRouteDefinition("doctor", "/v1/doctor"),
        AgentdRouteDefinition("audit", "/v1/audit"),
    )
)


_DETAIL_GET_ROUTES = AgentdRouteMatcher(
    (
        AgentdRouteDefinition("profile", "/v1/profiles/{profile}"),
        AgentdRouteDefinition("domain_package", "/v1/domain-packages/{name}"),
        AgentdRouteDefinition("domain_package_version", "/v1/domain-packages/{name}/{version}"),
    )
)


_DISTRIBUTED_ROUTES = AgentdRouteMatcher(
    (
        AgentdRouteDefinition(
            "distributed_worker_action",
            "/v1/distributed/workers/{worker_id}/{action}",
            ("POST",),
        ),
        AgentdRouteDefinition(
            "distributed_lock_acquire",
            "/v1/distributed/locks/acquire",
            ("POST",),
        ),
        AgentdRouteDefinition(
            "distributed_lock_lease_action",
            "/v1/distributed/lock-leases/{lease_id}/{action}",
            ("POST",),
        ),
        AgentdRouteDefinition("distributed_goals", "/v1/distributed/goals", ("POST",)),
        AgentdRouteDefinition(
            "distributed_pending_actions_schedule",
            "/v1/distributed/pending-actions/schedule",
            ("POST",),
        ),
        AgentdRouteDefinition(
            "distributed_schedule_action",
            "/v1/distributed/sessions/{session_id}/tasks/{task_id}/actions/{action_id}/schedule",
            ("POST",),
        ),
        AgentdRouteDefinition(
            "distributed_schedule_task",
            "/v1/distributed/sessions/{session_id}/tasks/{task_id}/schedule",
            ("POST",),
        ),
        AgentdRouteDefinition(
            "distributed_schedule_session",
            "/v1/distributed/sessions/{session_id}/schedule",
            ("POST",),
        ),
        AgentdRouteDefinition("distributed_expire", "/v1/distributed/expire", ("POST",)),
        AgentdRouteDefinition(
            "distributed_prune_terminal",
            "/v1/distributed/prune-terminal",
            ("POST",),
        ),
        AgentdRouteDefinition(
            "distributed_cancel",
            "/v1/distributed/work-items/{work_item_id}/cancel",
            ("POST",),
        ),
        AgentdRouteDefinition(
            "state_event_repair",
            "/v1/doctor/state-events/repair",
            ("POST",),
        ),
    )
)


_SESSION_ROUTES = AgentdRouteMatcher(
    (
        AgentdRouteDefinition("sessions", "/v1/sessions", ("GET", "POST")),
        AgentdRouteDefinition("session", "/v1/sessions/{session_id}"),
        AgentdRouteDefinition("session_diagnostics", "/v1/sessions/{session_id}/diagnostics"),
        AgentdRouteDefinition("session_evidence", "/v1/sessions/{session_id}/evidence"),
        AgentdRouteDefinition("session_world", "/v1/sessions/{session_id}/world"),
        AgentdRouteDefinition("session_events", "/v1/sessions/{session_id}/events"),
        AgentdRouteDefinition("session_events_stream", "/v1/sessions/{session_id}/events/stream"),
        AgentdRouteDefinition("session_audit", "/v1/sessions/{session_id}/audit"),
        AgentdRouteDefinition("session_cost", "/v1/sessions/{session_id}/cost"),
        AgentdRouteDefinition("session_logs", "/v1/sessions/{session_id}/logs"),
        AgentdRouteDefinition("session_traces", "/v1/sessions/{session_id}/traces"),
        AgentdRouteDefinition("session_traces_otlp", "/v1/sessions/{session_id}/traces/otlp"),
        AgentdRouteDefinition("session_pause", "/v1/sessions/{session_id}/pause", ("POST",)),
        AgentdRouteDefinition("session_resume", "/v1/sessions/{session_id}/resume", ("POST",)),
        AgentdRouteDefinition("session_cancel", "/v1/sessions/{session_id}/cancel", ("POST",)),
    )
)


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

        static_response = await self._static_get_route_response(request, method, path)
        if static_response is not None:
            return static_response

        console_response = await handle_console_route(
            self._service,
            self._evaluation_report_dir,
            request,
            method,
            path,
        )
        if console_response is not None:
            return console_response
        detail_response = await self._detail_get_route_response(method, path)
        if detail_response is not None:
            return detail_response

        distributed_response = await self._distributed_route_response(request, method, path)
        if distributed_response is not None:
            return distributed_response

        session_response = await self._session_route_response(request, method, path)
        if session_response is not None:
            return session_response

        return not_found(f"unknown route: {path}")

    async def _static_get_route_response(
        self,
        request: HttpRequest,
        method: str,
        path: str,
    ) -> HttpResponse | None:
        route = _STATIC_GET_ROUTES.match(path, method)
        if route is None:
            return None
        if not route.method_allowed:
            return method_not_allowed(route.allowed_methods)

        sync_json_handlers: dict[str, Callable[[], JsonMapping]] = {
            "health": lambda: health_body(self._service.health()),
            "ready": lambda: ready_body(self._service.ready()),
            "domains": lambda: immutable_json(
                {"domains": [domain_body(item) for item in self._service.domains()]}
            ),
            "capabilities": lambda: immutable_json(
                {"capabilities": [capability_body(item) for item in self._service.capabilities()]}
            ),
            "tools": lambda: immutable_json(
                {"tools": [tool_body(item) for item in self._service.tools()]}
            ),
            "policies": lambda: immutable_json(
                {"policies": [policy_body(item) for item in self._service.policies()]}
            ),
            "evaluators": lambda: immutable_json(
                {"evaluators": [evaluator_body(item) for item in self._service.evaluators()]}
            ),
            "memory": lambda: immutable_json(
                {"memories": [memory_body(item) for item in self._service.memories()]}
            ),
            "profiles": lambda: immutable_json(
                {"profiles": [profile_body(item) for item in self._service.profiles()]}
            ),
            "multi_agent": lambda: multi_agent_body(self._service.multi_agent()),
            "config": lambda: config_body(self._service.config()),
        }
        if handler := sync_json_handlers.get(route.name):
            return json_response(handler())

        if route.name == "domain_packages":
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
        if route.name == "distributed_snapshot":
            distributed_snapshot = self._service.distributed_snapshot()
            if distributed_snapshot is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_snapshot_body(distributed_snapshot))
        if route.name == "distributed_health":
            health = self._service.distributed_health()
            if health is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_health_body(health))
        if route.name == "metrics":
            return json_response(metrics_body(await self._service.metrics()))
        if route.name == "metrics_prometheus":
            return text_response(
                await self._service.prometheus_metrics(),
                content_type="text/plain; version=0.0.4; charset=utf-8",
            )
        if route.name == "cost":
            return json_response(cost_body(await self._service.cost()))
        if route.name == "logs":
            return json_response(log_records_body(await self._service.logs()))
        if route.name == "traces":
            return json_response(trace_spans_body(await self._service.traces()))
        if route.name == "traces_otlp":
            return json_response(await self._service.opentelemetry_traces())
        if route.name == "doctor":
            return json_response(doctor_body(await self._service.doctor()))
        if route.name == "audit":
            return json_response(audit_records_body(await self._service.audit_records()))
        return None

    async def _detail_get_route_response(self, method: str, path: str) -> HttpResponse | None:
        route = _DETAIL_GET_ROUTES.match(path, method)
        if route is None:
            return None
        if not route.method_allowed:
            return method_not_allowed(route.allowed_methods)

        if route.name == "profile":
            try:
                return json_response(
                    profile_body(self._service.profile(route.path_params["profile"]))
                )
            except ProfileNotFoundError as exc:
                return not_found(str(exc))
        if route.name in {"domain_package", "domain_package_version"}:
            try:
                return json_response(
                    domain_package_body(
                        self._service.domain_package(
                            route.path_params["name"],
                            route.path_params.get("version"),
                        )
                    )
                )
            except DomainPackageNotFoundError as exc:
                return not_found(str(exc))
            except AmbiguousDomainPackageError as exc:
                return bad_request(str(exc))
        return None

    async def _distributed_route_response(
        self,
        request: HttpRequest,
        method: str,
        path: str,
    ) -> HttpResponse | None:
        route = _DISTRIBUTED_ROUTES.match(path, method)
        if route is None:
            return None
        if not route.method_allowed:
            return method_not_allowed(route.allowed_methods)

        if route.name == "distributed_worker_action":
            return await self._distributed_worker_action_response(
                request,
                path,
                WorkerId(route.path_params["worker_id"]),
                route.path_params["action"],
            )
        if route.name == "distributed_lock_acquire":
            return self._distributed_lock_acquire_response(request)
        if route.name == "distributed_lock_lease_action":
            return self._distributed_lock_lease_action_response(
                request,
                path,
                DistributedLockLeaseId(route.path_params["lease_id"]),
                route.path_params["action"],
            )
        if route.name == "distributed_goals":
            return self._distributed_goals_response(request)
        if route.name == "distributed_pending_actions_schedule":
            return await self._distributed_pending_action_schedule_response(request)
        if route.name == "distributed_schedule_action":
            return self._distributed_schedule_action_response(
                request,
                SessionId(route.path_params["session_id"]),
                TaskId(route.path_params["task_id"]),
                ActionId(route.path_params["action_id"]),
            )
        if route.name == "distributed_schedule_task":
            return self._distributed_schedule_task_response(
                request,
                SessionId(route.path_params["session_id"]),
                TaskId(route.path_params["task_id"]),
            )
        if route.name == "distributed_schedule_session":
            return self._distributed_schedule_session_response(
                request,
                SessionId(route.path_params["session_id"]),
            )
        if route.name == "distributed_expire":
            maintenance = self._service.distributed_expire()
            if maintenance is None:
                return not_found("distributed runtime coordinator is not configured")
            return json_response(distributed_maintenance_body(maintenance))
        if route.name == "distributed_prune_terminal":
            return self._distributed_prune_terminal_response(request)
        if route.name == "distributed_cancel":
            return self._distributed_cancel_response(
                request,
                WorkItemId(route.path_params["work_item_id"]),
            )
        if route.name == "state_event_repair":
            return await self._state_event_repair_response(request)
        return None

    async def _distributed_worker_action_response(
        self,
        request: HttpRequest,
        path: str,
        worker_id: WorkerId,
        action: str,
    ) -> HttpResponse:
        try:
            lifecycle = None
            if action == "register":
                registration_payload = _distributed_worker_registration_payload(request.body)
                lifecycle = self._service.distributed_register_worker(
                    worker_id,
                    capabilities=registration_payload.capabilities,
                    metadata=registration_payload.metadata,
                    ttl_seconds=registration_payload.ttl_seconds,
                )
            elif action == "heartbeat":
                ttl_seconds = _distributed_worker_ttl_seconds(request.body)
                lifecycle = self._service.distributed_heartbeat_worker(
                    worker_id,
                    ttl_seconds=ttl_seconds,
                )
            elif action == "run-once":
                worker_run_payload = _distributed_worker_run_payload(request.body)
                worker_run = await self._service.distributed_run_worker_once(
                    worker_id,
                    lease_ttl_seconds=worker_run_payload.lease_ttl_seconds,
                    worker_ttl_seconds=worker_run_payload.worker_ttl_seconds,
                    heartbeat_interval_seconds=worker_run_payload.heartbeat_interval_seconds,
                )
                if worker_run is None:
                    return not_found("distributed runtime coordinator is not configured")
                return json_response(distributed_worker_run_body(worker_run))
            elif action == "run":
                worker_batch_payload = _distributed_worker_run_batch_payload(request.body)
                worker_runs = await self._service.distributed_run_worker_until_idle(
                    worker_id,
                    max_items=worker_batch_payload.max_items,
                    lease_ttl_seconds=worker_batch_payload.lease_ttl_seconds,
                    worker_ttl_seconds=worker_batch_payload.worker_ttl_seconds,
                    heartbeat_interval_seconds=worker_batch_payload.heartbeat_interval_seconds,
                )
                if worker_runs is None:
                    return not_found("distributed runtime coordinator is not configured")
                return json_response(distributed_worker_run_batch_body(worker_runs))
            elif action == "drain":
                lifecycle = self._service.distributed_drain_worker(
                    worker_id,
                    reason=_distributed_reason_payload(
                        request.body,
                        default="worker draining from agentd",
                        field_name="distributed worker drain reason",
                    ),
                )
            elif action == "offline":
                lifecycle = self._service.distributed_mark_worker_offline(
                    worker_id,
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

    def _distributed_lock_acquire_response(self, request: HttpRequest) -> HttpResponse:
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

    def _distributed_lock_lease_action_response(
        self,
        request: HttpRequest,
        path: str,
        lease_id: DistributedLockLeaseId,
        action: str,
    ) -> HttpResponse:
        try:
            lease_payload = _distributed_lock_lease_payload(request.body)
            if action == "heartbeat":
                lock_lifecycle = self._service.distributed_heartbeat_lock(
                    lease_id,
                    owner_id=lease_payload.owner_id,
                    ttl_seconds=lease_payload.ttl_seconds,
                )
            elif action == "release":
                lock_lifecycle = self._service.distributed_release_lock(
                    lease_id,
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

    def _distributed_goals_response(self, request: HttpRequest) -> HttpResponse:
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

    async def _distributed_pending_action_schedule_response(
        self,
        request: HttpRequest,
    ) -> HttpResponse:
        try:
            pending_action_schedule = _distributed_confirmed_schedule_payload(
                request.body,
                confirmed_field_name="distributed pending-action schedule confirmed",
            )
            if not pending_action_schedule.confirmed:
                return bad_request("distributed pending-action schedule requires confirmed=true")
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

    def _distributed_schedule_action_response(
        self,
        request: HttpRequest,
        session_id: SessionId,
        task_id: TaskId,
        action_id: ActionId,
    ) -> HttpResponse:
        try:
            action_schedule = _distributed_confirmed_schedule_payload(
                request.body,
                confirmed_field_name="distributed schedule-action confirmed",
            )
            if not action_schedule.confirmed:
                return bad_request("distributed schedule-action requires confirmed=true")
            scheduling = self._service.distributed_schedule_action(
                session_id,
                task_id,
                action_id,
                confirmed=action_schedule.confirmed,
                priority=action_schedule.priority,
                max_attempts=action_schedule.max_attempts,
            )
        except ValueError as exc:
            return bad_request(str(exc))
        if scheduling is None:
            return not_found("distributed runtime coordinator is not configured")
        return json_response(distributed_scheduling_body(scheduling))

    def _distributed_schedule_task_response(
        self,
        request: HttpRequest,
        session_id: SessionId,
        task_id: TaskId,
    ) -> HttpResponse:
        try:
            task_schedule = _distributed_schedule_payload(request.body)
            scheduling = self._service.distributed_schedule_task(
                session_id,
                task_id,
                payload=task_schedule.payload,
                priority=task_schedule.priority,
                max_attempts=task_schedule.max_attempts,
            )
        except ValueError as exc:
            return bad_request(str(exc))
        if scheduling is None:
            return not_found("distributed runtime coordinator is not configured")
        return json_response(distributed_scheduling_body(scheduling))

    def _distributed_schedule_session_response(
        self,
        request: HttpRequest,
        session_id: SessionId,
    ) -> HttpResponse:
        try:
            session_schedule = _distributed_schedule_payload(request.body)
            scheduling = self._service.distributed_schedule_session(
                session_id,
                payload=session_schedule.payload,
                priority=session_schedule.priority,
                max_attempts=session_schedule.max_attempts,
            )
        except ValueError as exc:
            return bad_request(str(exc))
        if scheduling is None:
            return not_found("distributed runtime coordinator is not configured")
        return json_response(distributed_scheduling_body(scheduling))

    def _distributed_prune_terminal_response(self, request: HttpRequest) -> HttpResponse:
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

    def _distributed_cancel_response(
        self,
        request: HttpRequest,
        work_item_id: WorkItemId,
    ) -> HttpResponse:
        try:
            cancel_reason = _distributed_reason_payload(
                request.body,
                default="distributed work item cancelled from agentd",
                field_name="distributed cancel reason",
            )
            cancellation = self._service.distributed_cancel_work_item(
                work_item_id,
                reason=cancel_reason,
            )
        except WorkItemNotFoundError as exc:
            return not_found(str(exc))
        except ValueError as exc:
            return bad_request(str(exc))
        if cancellation is None:
            return not_found("distributed runtime coordinator is not configured")
        return json_response(distributed_cancellation_body(cancellation))

    async def _state_event_repair_response(self, request: HttpRequest) -> HttpResponse:
        try:
            repair_payload = _state_event_repair_payload(request.body)
            report = await self._service.repair_state_event_consistency(
                confirmed=repair_payload.confirmed,
                dry_run=repair_payload.dry_run,
            )
        except ValueError as exc:
            return bad_request(str(exc))
        return json_response(state_event_repair_body(report))

    async def _session_route_response(
        self,
        request: HttpRequest,
        method: str,
        path: str,
    ) -> HttpResponse | None:
        route = _SESSION_ROUTES.match(path, method)
        if route is None:
            return None
        if not route.method_allowed:
            return method_not_allowed(route.allowed_methods)

        if route.name == "sessions":
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

        session_id = SessionId(route.path_params["session_id"])
        if route.name == "session":
            try:
                return json_response(session_body(await self._service.get_session(session_id)))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_diagnostics":
            try:
                return json_response(
                    session_explorer_body(await self._service.session_explorer(session_id))
                )
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_evidence":
            try:
                return json_response(
                    session_evidence_body(await self._service.session_explorer(session_id))
                )
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_world":
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
        if route.name == "session_events":
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
        if route.name == "session_events_stream":
            try:
                batch = await _stream_events_for_sse(self._service, session_id, request.path)
                return sse_event_batch_response(batch)
            except StateNotFoundError as exc:
                return not_found(str(exc))
            except ValueError as exc:
                return bad_request(str(exc))
        if route.name == "session_audit":
            try:
                return json_response(
                    audit_records_body(await self._service.audit_records(session_id))
                )
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_cost":
            try:
                return json_response(cost_body(await self._service.cost(session_id)))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_logs":
            try:
                return json_response(log_records_body(await self._service.logs(session_id)))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_traces":
            try:
                return json_response(trace_spans_body(await self._service.traces(session_id)))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_traces_otlp":
            try:
                return json_response(await self._service.opentelemetry_traces(session_id))
            except StateNotFoundError as exc:
                return not_found(str(exc))
        if route.name == "session_pause":
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
        if route.name == "session_resume":
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
        if route.name == "session_cancel":
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
        return None
