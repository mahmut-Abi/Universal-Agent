from __future__ import annotations

import asyncio
import hmac
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from urllib.parse import parse_qs, urlsplit

from universal_agent.core import (
    ActionId,
    EventId,
    ExecutionResult,
    Goal,
    JsonMapping,
    JsonValue,
    SessionId,
    SuccessCriterion,
    Task,
    TaskId,
    immutable_json,
)
from universal_agent.distributed import (
    DistributedCancellationResult,
    DistributedHealthReport,
    DistributedLockConflictError,
    DistributedLockLease,
    DistributedLockLeaseId,
    DistributedLockLeaseLostError,
    DistributedLockLifecycleResult,
    DistributedLockOwnerId,
    DistributedMaintenanceResult,
    DistributedRuntimeSnapshot,
    DistributedSchedulingResult,
    DistributedWorkerLifecycleResult,
    WorkerId,
    WorkerNotFoundError,
    WorkerRecord,
    WorkerRunResult,
    WorkItem,
    WorkItemId,
    WorkItemNotFoundError,
)
from universal_agent.domain import AmbiguousDomainPackageError, DomainPackageNotFoundError
from universal_agent.evaluation.console import (
    EvaluationConsoleSnapshot,
    build_evaluation_console_snapshot,
    render_evaluation_console,
)
from universal_agent.profile import ProfileNotFoundError
from universal_agent.runtime import (
    EvaluationView,
    EvidenceView,
    PendingActionView,
    RuntimeEventBatch,
    RuntimeEventView,
    RuntimeRun,
    RuntimeSessionBatch,
    SessionSummaryView,
    SessionView,
    TaskView,
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
    PolicyView,
    ProfileView,
    ReadyView,
    RuntimeConfigDomainView,
    RuntimeConfigView,
    RuntimeCostView,
    RuntimeLogRecordView,
    RuntimeMetricsView,
    RuntimeSecretRefView,
    RuntimeService,
    RuntimeTraceSpanView,
    SessionExplorerView,
    SessionWorldView,
    StateEventRepairReport,
    ToolView,
    WorldEntityView,
    WorldFactEvidenceView,
    WorldFactHistoryView,
    WorldFactView,
    WorldNeighborhoodView,
    WorldRelationView,
)
from universal_agent.state import StateNotFoundError
from universal_agent.web import (
    WebCatalogPage,
    WebConsoleSnapshot,
    build_web_console_snapshot,
    render_web_catalog,
    render_web_console,
    render_web_domain_detail,
    render_web_evidence_explorer,
    render_web_profile_catalog,
    render_web_session_detail,
    render_web_sessions,
    render_web_settings,
    render_web_world_model_explorer,
)


def _empty_json() -> JsonMapping:
    return immutable_json()


def _default_headers() -> Mapping[str, str]:
    return MappingProxyType({"content-type": "application/json"})


@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: str
    path: str
    body: JsonMapping = field(default_factory=_empty_json)
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: JsonMapping
    headers: Mapping[str, str] = field(default_factory=_default_headers)
    text_body: str | None = None


@dataclass(frozen=True, slots=True)
class GoalSubmission:
    goal: Goal
    task: Task
    profile_name: str | None = None


@dataclass(frozen=True, slots=True)
class AgentdAuthPolicy:
    bearer_token: str | None = None
    read_only_bearer_token: str | None = None
    public_paths: tuple[str, ...] = ("/health", "/ready")

    def __post_init__(self) -> None:
        _validate_bearer_token(self.bearer_token, "agentd bearer token")
        _validate_bearer_token(
            self.read_only_bearer_token,
            "agentd read-only bearer token",
        )
        if (
            self.bearer_token is not None
            and self.read_only_bearer_token is not None
            and hmac.compare_digest(self.bearer_token, self.read_only_bearer_token)
        ):
            raise ValueError("agentd bearer token and read-only bearer token must differ")
        if any(not path.startswith("/") or not path.strip() for path in self.public_paths):
            raise ValueError("agentd public paths must be absolute non-empty paths")

    @property
    def enabled(self) -> bool:
        return self.bearer_token is not None or self.read_only_bearer_token is not None


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
        if path == "/console/settings":
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                snapshot = await build_web_console_snapshot(
                    self._service,
                    session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                    event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
                )
            except ValueError as exc:
                return bad_request(str(exc))
            return text_response(
                render_web_settings(snapshot),
                content_type="text/html; charset=utf-8",
            )
        if path == "/console/profiles":
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                snapshot = await build_web_console_snapshot(
                    self._service,
                    session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                    event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
                )
            except ValueError as exc:
                return bad_request(str(exc))
            return text_response(
                render_web_profile_catalog(snapshot),
                content_type="text/html; charset=utf-8",
            )
        if path == "/console/evaluations":
            if method != "GET":
                return method_not_allowed(("GET",))
            evaluation_snapshot = (
                EvaluationConsoleSnapshot("not configured", ())
                if self._evaluation_report_dir is None
                else build_evaluation_console_snapshot(self._evaluation_report_dir)
            )
            return text_response(
                render_evaluation_console(evaluation_snapshot),
                content_type="text/html; charset=utf-8",
            )
        console_explorer = _console_explorer_renderer(path)
        if console_explorer is not None:
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                snapshot = await build_web_console_snapshot(
                    self._service,
                    session_id=_optional_session_id_query(request.path),
                    session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                    event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
                )
            except StateNotFoundError as exc:
                return not_found(str(exc))
            except ValueError as exc:
                return bad_request(str(exc))
            return text_response(
                console_explorer(snapshot),
                content_type="text/html; charset=utf-8",
            )
        console_catalog = _console_catalog_route(path)
        if console_catalog is not None:
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                snapshot = await build_web_console_snapshot(
                    self._service,
                    session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                    event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
                )
            except ValueError as exc:
                return bad_request(str(exc))
            return text_response(
                render_web_catalog(snapshot, console_catalog),
                content_type="text/html; charset=utf-8",
            )
        console_domain_name, console_domain_version = _console_domain_route(path)
        if console_domain_name is not None:
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                snapshot = await build_web_console_snapshot(
                    self._service,
                    session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                    event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
                )
            except ValueError as exc:
                return bad_request(str(exc))
            domain = _console_domain_view(snapshot, console_domain_name, console_domain_version)
            if domain is None:
                return not_found(
                    _domain_not_found_message(console_domain_name, console_domain_version)
                )
            return text_response(
                render_web_domain_detail(
                    snapshot,
                    domain_name=domain.name,
                    domain_version=domain.version,
                ),
                content_type="text/html; charset=utf-8",
            )
        if path == "/console/sessions":
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                snapshot = await build_web_console_snapshot(
                    self._service,
                    session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                    event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
                )
            except ValueError as exc:
                return bad_request(str(exc))
            return text_response(
                render_web_sessions(snapshot),
                content_type="text/html; charset=utf-8",
            )
        console_session_id, console_session_suffix = _console_session_route(path)
        if console_session_id is not None:
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                snapshot = await build_web_console_snapshot(
                    self._service,
                    session_id=console_session_id,
                    session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                    event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
                )
            except StateNotFoundError as exc:
                return not_found(str(exc))
            except ValueError as exc:
                return bad_request(str(exc))
            renderer = _console_session_renderer(console_session_suffix)
            if renderer is None:
                return not_found(f"unknown route: {path}")
            return text_response(
                renderer(snapshot),
                content_type="text/html; charset=utf-8",
            )
        if path in ("/", "/console"):
            if method != "GET":
                return method_not_allowed(("GET",))
            try:
                snapshot = await build_web_console_snapshot(
                    self._service,
                    session_id=_optional_session_id_query(request.path),
                    session_limit=_optional_positive_int_query(request.path, "session_limit") or 10,
                    event_limit=_optional_positive_int_query(request.path, "event_limit") or 20,
                )
            except StateNotFoundError as exc:
                return not_found(str(exc))
            except ValueError as exc:
                return bad_request(str(exc))
            return text_response(
                render_web_console(snapshot),
                content_type="text/html; charset=utf-8",
            )
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


def json_response(body: JsonMapping, *, status_code: int = 200) -> HttpResponse:
    return HttpResponse(status_code=status_code, body=body)


def text_response(
    text_body: str,
    *,
    status_code: int = 200,
    content_type: str = "text/plain; charset=utf-8",
) -> HttpResponse:
    return HttpResponse(
        status_code=status_code,
        body=immutable_json(),
        headers=MappingProxyType({"content-type": content_type}),
        text_body=text_body,
    )


def not_found(message: str) -> HttpResponse:
    return json_response(error_body("not_found", message), status_code=404)


def unauthorized() -> HttpResponse:
    return HttpResponse(
        status_code=401,
        body=error_body("unauthorized", "authentication required"),
        headers=MappingProxyType(
            {
                "content-type": "application/json",
                "www-authenticate": 'Bearer realm="agentd"',
            }
        ),
    )


def forbidden(message: str) -> HttpResponse:
    return json_response(error_body("forbidden", message), status_code=403)


def bad_request(message: str) -> HttpResponse:
    return json_response(error_body("bad_request", message), status_code=400)


def conflict(message: str) -> HttpResponse:
    return json_response(error_body("conflict", message), status_code=409)


def method_not_allowed(allowed: tuple[str, ...]) -> HttpResponse:
    headers = MappingProxyType(
        {
            "content-type": "application/json",
            "allow": ", ".join(allowed),
        }
    )
    return HttpResponse(
        status_code=405,
        body=error_body("method_not_allowed", "method is not allowed for this route"),
        headers=headers,
    )


def error_body(code: str, message: str) -> JsonMapping:
    return immutable_json({"error": {"code": code, "message": message}})


def _authenticate(
    policy: AgentdAuthPolicy,
    request: HttpRequest,
    path: str,
    *,
    method: str,
) -> HttpResponse | None:
    if not policy.enabled or path in policy.public_paths:
        return None
    token = _bearer_token(_header_value(request.headers, "authorization"))
    if token is None:
        return unauthorized()
    if _token_matches(token, policy.bearer_token):
        return None
    if _token_matches(token, policy.read_only_bearer_token):
        if method == "GET":
            return None
        return forbidden("insufficient bearer token scope")
    return unauthorized()


def _validate_bearer_token(value: str | None, field: str) -> None:
    if value is not None and not value.strip():
        raise ValueError(f"{field} must not be empty")


def _token_matches(token: str, expected: str | None) -> bool:
    if expected is None:
        return False
    return hmac.compare_digest(token, expected)


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    normalized = name.lower()
    for key, value in headers.items():
        if key.lower() == normalized:
            return value
    return None


def _bearer_token(value: str | None) -> str | None:
    if value is None:
        return None
    scheme, separator, token = value.strip().partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token:
        return None
    return token


def parse_goal_submission(body: JsonMapping) -> GoalSubmission:
    profile_name = _optional_non_empty_string_field(body, "profile", "profile")
    goal_payload = _object_field(body, "goal", "goal")
    task_payload = _object_field(body, "task", "task")
    goal = Goal(
        _non_empty_string_field(goal_payload, "description", "goal.description"),
        _success_criteria(goal_payload),
    )
    task = Task(
        _non_empty_string_field(task_payload, "description", "task.description"),
        _string_tuple_field(task_payload, "required_criteria", "task.required_criteria"),
    )
    return GoalSubmission(goal, task, profile_name)


def _success_criteria(payload: Mapping[str, JsonValue]) -> tuple[SuccessCriterion, ...]:
    items = _list_field(payload, "success_criteria", "goal.success_criteria")
    if not items:
        raise ValueError("goal.success_criteria must not be empty")
    criteria: list[SuccessCriterion] = []
    for index, item in enumerate(items):
        field = f"goal.success_criteria[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{field} must be an object")
        criteria.append(
            SuccessCriterion(
                _non_empty_string_field(item, "key", f"{field}.key"),
                _required_field(item, "expected", f"{field}.expected"),
            )
        )
    return tuple(criteria)


def _string_tuple_field(
    payload: Mapping[str, JsonValue],
    key: str,
    field: str,
) -> tuple[str, ...]:
    items = _list_field(payload, key, field)
    values: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, str):
            raise ValueError(f"{field}[{index}] must be a string")
        if not item.strip():
            raise ValueError(f"{field}[{index}] must not be empty")
        values.append(item)
    return tuple(values)


def _object_field(
    payload: Mapping[str, JsonValue], key: str, field: str
) -> Mapping[str, JsonValue]:
    value = _required_field(payload, key, field)
    if isinstance(value, dict):
        return value
    raise ValueError(f"{field} must be an object")


def _list_field(payload: Mapping[str, JsonValue], key: str, field: str) -> list[JsonValue]:
    value = _required_field(payload, key, field)
    if isinstance(value, list):
        return value
    raise ValueError(f"{field} must be a list")


def _non_empty_string_field(payload: Mapping[str, JsonValue], key: str, field: str) -> str:
    value = _required_field(payload, key, field)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value


def _optional_non_empty_string_field(
    payload: Mapping[str, JsonValue],
    key: str,
    field: str,
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value


def _required_field(payload: Mapping[str, JsonValue], key: str, field: str) -> JsonValue:
    try:
        return payload[key]
    except KeyError as exc:
        raise ValueError(f"{field} is required") from exc


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
        chunks.append(json.dumps(event_body(event), sort_keys=True))
        chunks.append("\n\n")
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
            "store": {
                "backend": view.store_backend,
                "path": view.store_path,
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


def session_body(view: SessionView) -> JsonMapping:
    return immutable_json(
        {
            "session_id": str(view.session_id),
            "goal_id": str(view.goal_id),
            "goal_description": view.goal_description,
            "goal_status": view.goal_status.value,
            "current_task_id": str(view.current_task_id),
            "current_task_description": view.current_task_description,
            "current_task_status": view.current_task_status.value,
            "iteration": view.iteration,
            "tasks": [task_body(item) for item in view.tasks],
            "satisfied_criteria": _json_value(view.satisfied_criteria),
            "pending_action": pending_action_body(view.pending_action),
            "latest_evaluation": evaluation_body(view.latest_evaluation),
            "termination_reason": view.termination_reason,
            "error_code": view.error_code.value if view.error_code is not None else None,
            "domain_name": view.domain_name,
            "domain_version": view.domain_version,
        }
    )


def session_explorer_body(view: SessionExplorerView) -> JsonMapping:
    return immutable_json(
        {
            "session": dict(session_body(view.session)),
            "evidence": [evidence_body(item) for item in view.evidence],
            "world_facts": [world_fact_body(item) for item in view.world_facts],
            "world_fact_histories": [
                world_fact_history_body(item) for item in view.world_fact_histories
            ],
            "world_entities": [world_entity_body(item) for item in view.world_entities],
            "world_relations": [world_relation_body(item) for item in view.world_relations],
        }
    )


def session_evidence_body(view: SessionExplorerView) -> JsonMapping:
    return immutable_json(
        {
            "session_id": str(view.session.session_id),
            "evidence": [evidence_body(item) for item in view.evidence],
        }
    )


def session_world_body(view: SessionWorldView) -> JsonMapping:
    return immutable_json(
        {
            "session_id": str(view.session_id),
            "world_facts": [world_fact_body(item) for item in view.world_facts],
            "world_fact_histories": [
                world_fact_history_body(item) for item in view.world_fact_histories
            ],
            "world_entities": [world_entity_body(item) for item in view.world_entities],
            "world_relations": [world_relation_body(item) for item in view.world_relations],
            "neighborhood": (
                None if view.neighborhood is None else world_neighborhood_body(view.neighborhood)
            ),
        }
    )


def evidence_body(view: EvidenceView) -> dict[str, JsonValue]:
    return {
        "evidence_id": str(view.evidence_id),
        "session_id": str(view.session_id),
        "task_id": str(view.task_id),
        "action_id": str(view.action_id),
        "observation_id": str(view.observation_id),
        "subject": view.subject,
        "claim": view.claim,
        "value": _json_value(view.value),
        "source": view.source,
        "confidence": view.confidence,
        "observed_at": view.observed_at.isoformat(),
    }


def world_fact_body(view: WorldFactView) -> dict[str, JsonValue]:
    return {
        "subject": view.subject,
        "claim": view.claim,
        "value": _json_value(view.value),
        "confidence": view.confidence,
        "observed_at": view.observed_at.isoformat(),
        "evidence_ids": list(view.evidence_ids),
    }


def world_fact_evidence_body(view: WorldFactEvidenceView) -> dict[str, JsonValue]:
    return {
        "evidence_id": view.evidence_id,
        "value": _json_value(view.value),
        "confidence": view.confidence,
        "observed_at": view.observed_at.isoformat(),
        "source": view.source,
    }


def world_fact_history_body(view: WorldFactHistoryView) -> dict[str, JsonValue]:
    return {
        "subject": view.subject,
        "claim": view.claim,
        "current": world_fact_body(view.current),
        "candidates": [world_fact_evidence_body(item) for item in view.candidates],
        "conflicting": view.conflicting,
    }


def world_entity_body(view: WorldEntityView) -> dict[str, JsonValue]:
    return {
        "entity_id": view.entity_id,
        "kind": view.kind,
        "attributes": _json_value(view.attributes),
        "evidence_ids": list(view.evidence_ids),
    }


def world_relation_body(view: WorldRelationView) -> dict[str, JsonValue]:
    return {
        "source": view.source,
        "relation": view.relation,
        "target": view.target,
        "evidence_ids": list(view.evidence_ids),
    }


def world_neighborhood_body(view: WorldNeighborhoodView) -> dict[str, JsonValue]:
    return {
        "root": None if view.root is None else world_entity_body(view.root),
        "facts": [world_fact_body(item) for item in view.facts],
        "outgoing_relations": [world_relation_body(item) for item in view.outgoing_relations],
        "incoming_relations": [world_relation_body(item) for item in view.incoming_relations],
        "related_entities": [world_entity_body(item) for item in view.related_entities],
    }


def session_summary_body(view: SessionSummaryView) -> dict[str, JsonValue]:
    return {
        "session_id": str(view.session_id),
        "goal_id": str(view.goal_id),
        "goal_description": view.goal_description,
        "goal_status": view.goal_status.value,
        "current_task_id": str(view.current_task_id),
        "current_task_description": view.current_task_description,
        "current_task_status": view.current_task_status.value,
        "iteration": view.iteration,
        "task_count": view.task_count,
        "pending_action": view.pending_action,
        "termination_reason": view.termination_reason,
        "error_code": view.error_code.value if view.error_code is not None else None,
        "domain_name": view.domain_name,
        "domain_version": view.domain_version,
        "created_at": view.created_at.isoformat(),
    }


def task_body(view: TaskView) -> dict[str, JsonValue]:
    return {
        "task_id": str(view.task_id),
        "description": view.description,
        "status": view.status.value,
        "required_criteria": list(view.required_criteria),
        "depends_on": [str(item) for item in view.depends_on],
    }


def pending_action_body(view: PendingActionView | None) -> JsonValue:
    if view is None:
        return None
    return {
        "action_id": str(view.action_id),
        "capability": view.capability,
        "tool_name": view.tool_name,
        "target": view.target,
        "arguments": _json_value(view.arguments),
        "domain_name": view.domain_name,
        "domain_version": view.domain_version,
        "idempotency_key": view.idempotency_key,
        "parameters_hash": view.parameters_hash,
        "attempt": view.attempt,
        "resource_key": view.resource_key,
        "resource_version": view.resource_version,
    }


def evaluation_body(view: EvaluationView | None) -> JsonValue:
    if view is None:
        return None
    return {
        "status": view.status.value,
        "reason": view.reason,
        "evaluator_name": view.evaluator_name,
        "matched_criteria": _json_value(view.matched_criteria),
        "task_completed": view.task_completed,
        "goal_completed": view.goal_completed,
    }


def event_body(view: RuntimeEventView) -> dict[str, JsonValue]:
    return {
        "event_id": view.event_id,
        "type": view.type,
        "session_id": str(view.session_id),
        "goal_id": str(view.goal_id),
        "task_id": str(view.task_id),
        "action_id": str(view.action_id) if view.action_id is not None else None,
        "data": _json_value(view.data),
        "occurred_at": view.occurred_at.isoformat(),
    }


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_value(item) for item in value]
    return str(value)


def _normalize_path(path: str) -> str:
    normalized = urlsplit(path).path
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    normalized = normalized.rstrip("/")
    return normalized or "/"


def _optional_event_cursor(path: str) -> EventId | None:
    value = _optional_query_value(path, "after")
    if value is None:
        return None
    return EventId(value)


def _optional_session_cursor(path: str) -> SessionId | None:
    value = _optional_query_value(path, "after")
    if value is None:
        return None
    return SessionId(value)


def _optional_session_id_query(path: str) -> SessionId | None:
    value = _optional_query_value(path, "session_id")
    if value is None:
        return None
    return SessionId(value)


def _optional_bool_query(path: str, key: str) -> bool | None:
    value = _optional_query_value(path, key)
    if value is None:
        return None
    normalized = value.lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"{key} must be a boolean")


def _optional_float_query(
    path: str,
    key: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = _optional_query_value(path, key)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{key} must be between {minimum:g} and {maximum:g}")
    return parsed


def _optional_positive_int_query(path: str, key: str) -> int | None:
    value = _optional_query_value(path, key)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{key} must be a positive integer")
    return parsed


def _optional_query_value(path: str, key: str) -> str | None:
    values = parse_qs(urlsplit(path).query, keep_blank_values=True).get(key)
    if values is None:
        return None
    if len(values) != 1:
        raise ValueError(f"{key} must be specified once")
    value = values[0]
    if not value.strip():
        raise ValueError(f"{key} must not be empty")
    return value


def _session_route(path: str) -> tuple[SessionId | None, str]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if len(segments) == 3 and segments[:2] == ("v1", "sessions"):
        return SessionId(segments[2]), ""
    if len(segments) == 4 and segments[:2] == ("v1", "sessions"):
        return SessionId(segments[2]), segments[3]
    if len(segments) == 5 and segments[:2] == ("v1", "sessions"):
        return SessionId(segments[2]), f"{segments[3]}/{segments[4]}"
    return None, ""


def _console_session_route(path: str) -> tuple[SessionId | None, str]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if len(segments) == 3 and segments[:2] == ("console", "sessions") and segments[2].strip():
        return SessionId(segments[2]), ""
    if len(segments) == 4 and segments[:2] == ("console", "sessions") and segments[2].strip():
        return SessionId(segments[2]), segments[3]
    return None, ""


def _console_catalog_route(path: str) -> WebCatalogPage | None:
    segments = tuple(segment for segment in path.split("/") if segment)
    if len(segments) != 2 or segments[0] != "console":
        return None
    try:
        return WebCatalogPage(segments[1])
    except ValueError:
        return None


def _console_domain_route(path: str) -> tuple[str | None, str | None]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if len(segments) == 3 and segments[:2] == ("console", "domains") and segments[2].strip():
        return segments[2], None
    if (
        len(segments) == 4
        and segments[:2] == ("console", "domains")
        and segments[2].strip()
        and segments[3].strip()
    ):
        return segments[2], segments[3]
    return None, None


def _distributed_lock_lease_route(path: str) -> tuple[DistributedLockLeaseId | None, str]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if (
        len(segments) == 5
        and segments[:3] == ("v1", "distributed", "lock-leases")
        and segments[3].strip()
        and segments[4].strip()
    ):
        return DistributedLockLeaseId(segments[3]), segments[4]
    return None, ""


def _distributed_lock_owner_id(body: JsonMapping) -> DistributedLockOwnerId:
    return DistributedLockOwnerId(
        _distributed_required_string(
            body,
            key="owner_id",
            field_name="distributed lock owner_id",
        )
    )


def _distributed_lock_ttl_seconds(body: JsonMapping) -> float:
    value = body.get("ttl_seconds", 30.0)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("distributed lock ttl_seconds must be a positive number")
    ttl_seconds = float(value)
    if ttl_seconds <= 0:
        raise ValueError("distributed lock ttl_seconds must be a positive number")
    return ttl_seconds


def _distributed_required_string(
    body: JsonMapping,
    *,
    key: str,
    field_name: str,
) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _distributed_worker_action_route(path: str) -> tuple[WorkerId | None, str]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if (
        len(segments) == 5
        and segments[:3] == ("v1", "distributed", "workers")
        and segments[3].strip()
        and segments[4].strip()
    ):
        return WorkerId(segments[3]), segments[4]
    return None, ""


def _distributed_worker_capabilities(body: JsonMapping) -> tuple[str, ...]:
    value = body.get("capabilities", ())
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError("distributed worker capabilities must be a list of strings")
    capabilities: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"distributed worker capabilities[{index}] must be a non-empty string")
        capabilities.append(item)
    return tuple(capabilities)


def _distributed_ttl_seconds(body: JsonMapping) -> float:
    value = body.get("ttl_seconds", 30.0)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("distributed worker ttl_seconds must be a positive number")
    ttl_seconds = float(value)
    if ttl_seconds <= 0:
        raise ValueError("distributed worker ttl_seconds must be a positive number")
    return ttl_seconds


def _distributed_worker_run_seconds(
    body: JsonMapping,
    *,
    field_name: str,
    default: float,
) -> float:
    value = body.get(field_name, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"distributed worker {field_name} must be a positive number")
    seconds = float(value)
    if seconds <= 0:
        raise ValueError(f"distributed worker {field_name} must be a positive number")
    return seconds


def _distributed_worker_run_optional_seconds(
    body: JsonMapping,
    *,
    field_name: str,
) -> float | None:
    if field_name not in body:
        return None
    return _distributed_worker_run_seconds(body, field_name=field_name, default=30.0)


def _distributed_worker_run_max_items(body: JsonMapping) -> int:
    value = body.get("max_items", 1)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("distributed worker max_items must be a positive integer")
    if value < 1:
        raise ValueError("distributed worker max_items must be a positive integer")
    return value


def _distributed_reason(
    body: JsonMapping,
    *,
    default: str,
    field_name: str,
) -> str:
    value = body.get("reason", default)
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _distributed_schedule_session_route(path: str) -> SessionId | None:
    segments = tuple(segment for segment in path.split("/") if segment)
    if (
        len(segments) == 5
        and segments[:3] == ("v1", "distributed", "sessions")
        and segments[3].strip()
        and segments[4] == "schedule"
    ):
        return SessionId(segments[3])
    return None


def _distributed_schedule_task_route(path: str) -> tuple[SessionId | None, TaskId | None]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if (
        len(segments) == 7
        and segments[:3] == ("v1", "distributed", "sessions")
        and segments[3].strip()
        and segments[4] == "tasks"
        and segments[5].strip()
        and segments[6] == "schedule"
    ):
        return SessionId(segments[3]), TaskId(segments[5])
    return None, None


def _distributed_schedule_action_route(
    path: str,
) -> tuple[SessionId | None, TaskId | None, ActionId | None]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if (
        len(segments) == 9
        and segments[:3] == ("v1", "distributed", "sessions")
        and segments[3].strip()
        and segments[4] == "tasks"
        and segments[5].strip()
        and segments[6] == "actions"
        and segments[7].strip()
        and segments[8] == "schedule"
    ):
        return SessionId(segments[3]), TaskId(segments[5]), ActionId(segments[7])
    return None, None, None


def _distributed_cancel_route(path: str) -> WorkItemId | None:
    segments = tuple(segment for segment in path.split("/") if segment)
    if (
        len(segments) == 5
        and segments[:3] == ("v1", "distributed", "work-items")
        and segments[3].strip()
        and segments[4] == "cancel"
    ):
        return WorkItemId(segments[3])
    return None


def _console_domain_view(
    snapshot: WebConsoleSnapshot,
    name: str,
    version: str | None,
) -> DomainView | None:
    matches = tuple(
        domain
        for domain in snapshot.domains
        if domain.name == name and (version is None or domain.version == version)
    )
    if len(matches) != 1:
        return None
    return matches[0]


def _domain_not_found_message(name: str, version: str | None) -> str:
    if version is None:
        return f"domain not found or ambiguous: {name}"
    return f"domain not found: {name}@{version}"


def _console_explorer_renderer(
    path: str,
) -> Callable[[WebConsoleSnapshot], str] | None:
    if path == "/console/evidence":
        return render_web_evidence_explorer
    if path == "/console/world":
        return render_web_world_model_explorer
    return None


def _console_session_renderer(
    suffix: str,
) -> Callable[[WebConsoleSnapshot], str] | None:
    if suffix == "":
        return render_web_session_detail
    if suffix == "evidence":
        return render_web_evidence_explorer
    if suffix == "world":
        return render_web_world_model_explorer
    return None


def _profile_route(path: str) -> str | None:
    segments = tuple(segment for segment in path.split("/") if segment)
    if len(segments) == 3 and segments[:2] == ("v1", "profiles") and segments[2].strip():
        return segments[2]
    return None


def _domain_package_route(path: str) -> tuple[str | None, str | None]:
    segments = tuple(segment for segment in path.split("/") if segment)
    if len(segments) == 3 and segments[:2] == ("v1", "domain-packages") and segments[2].strip():
        return segments[2], None
    if (
        len(segments) == 4
        and segments[:2] == ("v1", "domain-packages")
        and segments[2].strip()
        and segments[3].strip()
    ):
        return segments[2], segments[3]
    return None, None
