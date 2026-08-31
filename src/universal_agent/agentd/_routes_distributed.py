from __future__ import annotations

from universal_agent.agentd.http import (
    HttpRequest,
    HttpResponse,
    _optional_datetime_field,
    bad_request,
    conflict,
    json_response,
    method_not_allowed,
    not_found,
    parse_goal_submission,
)
from universal_agent.agentd.representations import (
    distributed_cancellation_body,
    distributed_lock_lifecycle_body,
    distributed_maintenance_body,
    distributed_pending_action_scheduling_body,
    distributed_prune_body,
    distributed_scheduling_body,
    distributed_worker_lifecycle_body,
    distributed_worker_run_batch_body,
    distributed_worker_run_body,
    state_event_repair_body,
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
    _state_event_repair_payload,
)
from universal_agent.core import ActionId, SessionId, TaskId
from universal_agent.distributed import (
    DistributedLockConflictError,
    DistributedLockLeaseId,
    DistributedLockLeaseLostError,
    WorkerId,
    WorkerNotFoundError,
    WorkItemId,
    WorkItemNotFoundError,
)
from universal_agent.service import RuntimeService

_DISTRIBUTED_ROUTE_DEFINITIONS = (
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
_DISTRIBUTED_ROUTES = AgentdRouteMatcher(_DISTRIBUTED_ROUTE_DEFINITIONS)


class DistributedRouteHandlers:
    """HTTP route handlers for the distributed runtime coordinator surface.

    Pure JSON/HTTP adaptation. All runtime behavior stays behind RuntimeService;
    this class only translates requests/responses for the distributed control plane.
    """

    def __init__(self, service: RuntimeService) -> None:
        self._service = service

    async def route_response(
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
                    capabilities=registration_payload.capability_tuple,
                    metadata=registration_payload.metadata_mapping,
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
                owner_id=lock_payload.lock_owner_id,
                ttl_seconds=lock_payload.ttl_seconds,
                metadata=lock_payload.metadata_mapping,
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
                    owner_id=lease_payload.lock_owner_id,
                    ttl_seconds=lease_payload.ttl_seconds,
                )
            elif action == "release":
                lock_lifecycle = self._service.distributed_release_lock(
                    lease_id,
                    owner_id=lease_payload.lock_owner_id,
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
        if submission.task is None:
            return bad_request("distributed goal scheduling requires an explicit task")
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
                payload=task_schedule.payload_mapping,
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
                payload=session_schedule.payload_mapping,
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
