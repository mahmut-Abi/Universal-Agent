from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, cast

from universal_agent.core import (
    ActionId,
    CapabilityCategory,
    DomainIdentity,
    EventId,
    ExecutionStatus,
    Goal,
    GoalId,
    GoalStatus,
    JsonMapping,
    JsonValue,
    PolicyEffect,
    RiskLevel,
    SessionId,
    SideEffect,
    SuccessCriterion,
    Task,
    TaskId,
    immutable_json,
    utc_now,
)
from universal_agent.distributed import (
    DistributedCancellationResult,
    DistributedHealthReport,
    DistributedLockLeaseId,
    DistributedLockLifecycleResult,
    DistributedLockOwnerId,
    DistributedMaintenanceResult,
    DistributedRuntimeCoordinator,
    DistributedRuntimeSnapshot,
    DistributedSchedulingResult,
    DistributedWorkerLifecycleResult,
    WorkerId,
    WorkerRunResult,
    WorkHandler,
    WorkHandlerResult,
    WorkItem,
    WorkItemId,
    WorkKind,
    WorkQueueWorker,
)
from universal_agent.domain import (
    ActiveDomain,
    DomainPackage,
    DomainPackageRegistry,
    RuntimeComponents,
)
from universal_agent.evidence import Evidence
from universal_agent.memory import MemoryKind, MemoryRecord
from universal_agent.operations import (
    AuditRecordView,
    DoctorReportView,
    RuntimeCostView,
    RuntimeLogRecordView,
    RuntimeMetricsView,
    RuntimeTraceSpanView,
    build_audit_records,
    build_doctor_report,
    build_opentelemetry_trace_export,
    build_prometheus_metrics_export,
    build_runtime_cost,
    build_runtime_logs,
    build_runtime_metrics,
    build_runtime_trace_spans,
)
from universal_agent.policy import Policy, PolicyRule
from universal_agent.profile import AgentProfile, ProfileNotFoundError, ProfileRegistry
from universal_agent.runtime import (
    EvidenceView,
    RuntimeAPI,
    RuntimeEventBatch,
    RuntimeEventView,
    RuntimeRun,
    RuntimeSessionBatch,
    SessionSummaryView,
    SessionView,
)
from universal_agent.state import StateNotFoundError
from universal_agent.world import InMemoryWorldModel, WorldFact

if TYPE_CHECKING:
    from universal_agent.host.config import RuntimeConfig


@dataclass(frozen=True, slots=True)
class HealthView:
    status: str
    service: str


@dataclass(frozen=True, slots=True)
class ReadyView:
    ready: bool
    reason: str
    domain_count: int
    capability_count: int
    tool_count: int


@dataclass(frozen=True, slots=True)
class DomainView:
    name: str
    version: str
    description: str
    primary: bool
    ontology: tuple[str, ...]
    capability_names: tuple[str, ...]
    evaluator_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DomainPackageView:
    name: str
    version: str
    description: str
    author: str | None
    entrypoint: str | None
    tags: tuple[str, ...]
    ontology: tuple[str, ...]
    capability_names: tuple[str, ...]
    tool_names: tuple[str, ...]
    policy_names: tuple[str, ...]
    procedure_names: tuple[str, ...]
    knowledge_names: tuple[str, ...]
    evaluator_names: tuple[str, ...]
    context_provider_names: tuple[str, ...]
    prompt_names: tuple[str, ...]
    dependencies: tuple[DomainIdentity, ...]
    required_tools: tuple[str, ...]
    runtime_api_compatibility: str | None
    domain_api_compatibility: str | None
    security: JsonMapping
    root_path: str
    manifest_path: str


@dataclass(frozen=True, slots=True)
class CapabilityView:
    name: str
    description: str
    category: CapabilityCategory
    risk: RiskLevel
    domain_name: str
    domain_version: str
    tool_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ToolView:
    name: str
    description: str
    capabilities: tuple[str, ...]
    required_arguments: tuple[str, ...]
    side_effect: SideEffect
    risk: RiskLevel
    timeout_seconds: float
    priority: int
    domain_name: str
    domain_version: str


@dataclass(frozen=True, slots=True)
class PolicyView:
    name: str
    description: str
    policy_type: str
    effect: PolicyEffect | None
    capability_names: tuple[str, ...]
    categories: tuple[CapabilityCategory, ...]
    risks: tuple[RiskLevel, ...]
    domain_name: str
    domain_version: str


@dataclass(frozen=True, slots=True)
class EvaluatorView:
    name: str
    evaluator_type: str
    domain_name: str
    domain_version: str


@dataclass(frozen=True, slots=True)
class MemoryView:
    memory_id: str
    kind: MemoryKind
    subject: str
    content: str
    scope: str
    confidence: float
    source_session_id: SessionId | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProfileView:
    name: str
    version: str
    description: str
    domain_name: str
    domain_version: str
    domains: tuple[DomainIdentity, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeConfigDomainView:
    name: str
    version: str
    primary: bool


@dataclass(frozen=True, slots=True)
class RuntimeConfigView:
    environment: JsonMapping
    store_backend: str
    store_path: str | None
    distributed_queue_backend: str
    distributed_queue_path: str | None
    max_iterations: int
    max_recovery_steps: int
    domains: tuple[RuntimeConfigDomainView, ...]


@dataclass(frozen=True, slots=True)
class WorldFactView:
    subject: str
    claim: str
    value: JsonValue
    confidence: float
    observed_at: datetime
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SessionExplorerView:
    session: SessionView
    evidence: tuple[EvidenceView, ...]
    world_facts: tuple[WorldFactView, ...]


class RuntimeService:
    """Application-facing service module for future agentd adapters.

    Execution and lifecycle control flow through RuntimeAPI. This service adds
    product-level health, readiness and catalog metadata over RuntimeComponents.
    """

    def __init__(
        self,
        *,
        runtime_api: RuntimeAPI,
        components: RuntimeComponents,
        profiles: tuple[AgentProfile, ...] = (),
        config: RuntimeConfig | None = None,
        distributed_coordinator: DistributedRuntimeCoordinator | None = None,
        domain_packages: DomainPackageRegistry | None = None,
    ) -> None:
        self._runtime_api = runtime_api
        self._components = components
        self._profiles = ProfileRegistry(profiles)
        self._config = config
        self._distributed_coordinator = distributed_coordinator
        self._domain_packages = domain_packages or DomainPackageRegistry()

    def health(self) -> HealthView:
        return HealthView(status="ok", service="universal-agent-runtime")

    def ready(self) -> ReadyView:
        domains = self._components.domain_composition.domains
        capabilities = self._components.capabilities.all()
        tools = self._components.tools.all()
        missing_tools = tuple(
            capability.name
            for capability in capabilities
            if not self._components.tools.registrations_for_capability(capability.name)
        )
        ready = bool(domains) and bool(capabilities) and bool(tools) and not missing_tools
        reason = (
            "ready"
            if ready
            else _not_ready_reason(
                has_domains=bool(domains),
                has_capabilities=bool(capabilities),
                has_tools=bool(tools),
                missing_tools=missing_tools,
            )
        )
        return ReadyView(
            ready=ready,
            reason=reason,
            domain_count=len(domains),
            capability_count=len(capabilities),
            tool_count=len(tools),
        )

    def domains(self) -> tuple[DomainView, ...]:
        primary = self._components.domain_composition.primary.identity
        return tuple(
            domain_view(domain, primary=domain.identity == primary)
            for domain in self._components.domain_composition.domains
        )

    def domain_packages(self, *, tag: str | None = None) -> tuple[DomainPackageView, ...]:
        return tuple(
            domain_package_view(package) for package in self._domain_packages.list(tag=tag)
        )

    def domain_package(self, name: str, version: str | None = None) -> DomainPackageView:
        package = (
            self._domain_packages.get_by_name(name)
            if version is None
            else self._domain_packages.get(DomainIdentity(name, version))
        )
        return domain_package_view(package)

    def capabilities(self) -> tuple[CapabilityView, ...]:
        views: list[CapabilityView] = []
        for domain in self._components.domain_composition.domains:
            for capability in domain.capabilities:
                tool_names = tuple(
                    registration.tool.definition.name
                    for registration in sorted(
                        self._components.tools.registrations_for_capability(capability.name),
                        key=lambda item: item.tool.definition.name,
                    )
                )
                views.append(
                    CapabilityView(
                        name=capability.name,
                        description=capability.description,
                        category=capability.category,
                        risk=capability.risk,
                        domain_name=domain.identity.name,
                        domain_version=domain.identity.version,
                        tool_names=tool_names,
                    )
                )
        return tuple(sorted(views, key=lambda item: item.name))

    def tools(self) -> tuple[ToolView, ...]:
        views: list[ToolView] = []
        for domain in self._components.domain_composition.domains:
            for tool in domain.tools:
                definition = tool.definition
                views.append(
                    ToolView(
                        name=definition.name,
                        description=definition.description,
                        capabilities=definition.capabilities,
                        required_arguments=definition.required_arguments,
                        side_effect=definition.side_effect,
                        risk=definition.risk,
                        timeout_seconds=definition.timeout_seconds,
                        priority=definition.priority,
                        domain_name=domain.identity.name,
                        domain_version=domain.identity.version,
                    )
                )
        return tuple(sorted(views, key=lambda item: item.name))

    def policies(self) -> tuple[PolicyView, ...]:
        views: list[PolicyView] = []
        for domain in self._components.domain_composition.domains:
            views.extend(policy_view(policy, domain) for policy in domain.policies)
        return tuple(sorted(views, key=lambda item: item.name))

    def evaluators(self) -> tuple[EvaluatorView, ...]:
        views: list[EvaluatorView] = []
        for domain in self._components.domain_composition.domains:
            views.extend(evaluator_view(evaluator, domain) for evaluator in domain.evaluators)
        return tuple(sorted(views, key=lambda item: item.name))

    def memories(self) -> tuple[MemoryView, ...]:
        return tuple(memory_view(record) for record in self._components.memory_store.export())

    def profiles(self) -> tuple[ProfileView, ...]:
        return tuple(profile_view(profile) for profile in self._profiles.all())

    def profile(self, name: str) -> ProfileView:
        return profile_view(self._profiles.get(name))

    def accepts_profile(self, name: str) -> bool:
        return self.profile_selection_error(name) is None

    def profile_selection_error(self, name: str) -> str | None:
        try:
            profile = self._profiles.get(name)
        except ProfileNotFoundError:
            return f"unknown profile: {name}"
        profile_domains = tuple(domain.identity() for domain in profile.configured_domains())
        active_domains = self._components.domain_composition.identities
        if profile_domains != active_domains:
            return (
                f"profile {name} is not bound to this RuntimeService: profile domains "
                f"{_format_identities(profile_domains)} do not match active runtime domains "
                f"{_format_identities(active_domains)}"
            )
        return None

    def config(self) -> RuntimeConfigView:
        identities = self._components.domain_composition.identities
        if self._config is None:
            return RuntimeConfigView(
                environment=immutable_json(),
                store_backend="memory",
                store_path=None,
                distributed_queue_backend="memory",
                distributed_queue_path=None,
                max_iterations=20,
                max_recovery_steps=8,
                domains=runtime_config_domain_views(identities),
            )
        configured = tuple(
            DomainIdentity(domain.name, domain.version)
            for domain in self._config.configured_domains()
            if domain.name is not None and domain.version is not None
        )
        return RuntimeConfigView(
            environment=immutable_json(self._config.environment),
            store_backend=self._config.store.backend.value,
            store_path=self._config.store.path,
            distributed_queue_backend=self._config.distributed_queue.backend.value,
            distributed_queue_path=self._config.distributed_queue.path,
            max_iterations=self._config.limits.max_iterations,
            max_recovery_steps=self._config.limits.max_recovery_steps,
            domains=runtime_config_domain_views(configured or identities),
        )

    def distributed_snapshot(self) -> DistributedRuntimeSnapshot | None:
        if self._distributed_coordinator is None:
            return None
        return self._distributed_coordinator.snapshot()

    def distributed_health(self, *, now: datetime | None = None) -> DistributedHealthReport | None:
        if self._distributed_coordinator is None:
            return None
        return self._distributed_coordinator.health(now=now)

    def distributed_schedule_session(
        self,
        session_id: SessionId,
        *,
        payload: JsonMapping | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> DistributedSchedulingResult | None:
        if self._distributed_coordinator is None:
            return None
        return self._distributed_coordinator.schedule_session(
            session_id,
            payload=payload,
            priority=priority,
            max_attempts=max_attempts,
            available_at=now,
            now=now,
        )

    def distributed_schedule_goal(
        self,
        goal: Goal,
        task: Task,
        *,
        priority: int = 0,
        max_attempts: int = 3,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> DistributedSchedulingResult | None:
        if self._distributed_coordinator is None:
            return None
        return self._distributed_coordinator.schedule_goal(
            payload=_goal_work_payload(goal, task),
            idempotency_key=idempotency_key or _goal_work_idempotency_key(goal, task),
            priority=priority,
            max_attempts=max_attempts,
            available_at=now,
            now=now,
        )

    def distributed_schedule_task(
        self,
        session_id: SessionId,
        task_id: TaskId,
        *,
        payload: JsonMapping | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> DistributedSchedulingResult | None:
        if self._distributed_coordinator is None:
            return None
        return self._distributed_coordinator.schedule_task(
            session_id,
            task_id,
            payload=payload,
            priority=priority,
            max_attempts=max_attempts,
            available_at=now,
            now=now,
        )

    def distributed_schedule_action(
        self,
        session_id: SessionId,
        task_id: TaskId,
        action_id: ActionId,
        *,
        confirmed: bool,
        priority: int = 0,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> DistributedSchedulingResult | None:
        if not confirmed:
            raise ValueError("distributed schedule-action requires confirmed=true")
        if self._distributed_coordinator is None:
            return None
        return self._distributed_coordinator.schedule_action(
            session_id,
            task_id,
            action_id,
            payload=immutable_json({"confirmed": confirmed}),
            priority=priority,
            max_attempts=max_attempts,
            available_at=now,
            now=now,
        )

    def distributed_register_worker(
        self,
        worker_id: WorkerId,
        *,
        capabilities: tuple[str, ...] = (),
        metadata: JsonMapping | None = None,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> DistributedWorkerLifecycleResult | None:
        if self._distributed_coordinator is None:
            return None
        return self._distributed_coordinator.register_worker(
            worker_id,
            capabilities=capabilities,
            metadata=metadata,
            ttl_seconds=ttl_seconds,
            now=now,
        )

    def distributed_heartbeat_worker(
        self,
        worker_id: WorkerId,
        *,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> DistributedWorkerLifecycleResult | None:
        if self._distributed_coordinator is None:
            return None
        return self._distributed_coordinator.heartbeat_worker(
            worker_id,
            ttl_seconds=ttl_seconds,
            now=now,
        )

    def distributed_drain_worker(
        self,
        worker_id: WorkerId,
        *,
        reason: str = "worker draining",
        now: datetime | None = None,
    ) -> DistributedWorkerLifecycleResult | None:
        if self._distributed_coordinator is None:
            return None
        return self._distributed_coordinator.drain_worker(
            worker_id,
            reason=reason,
            now=now,
        )

    def distributed_mark_worker_offline(
        self,
        worker_id: WorkerId,
        *,
        reason: str = "worker offline",
        now: datetime | None = None,
    ) -> DistributedWorkerLifecycleResult | None:
        if self._distributed_coordinator is None:
            return None
        return self._distributed_coordinator.mark_worker_offline(
            worker_id,
            reason=reason,
            now=now,
        )

    def distributed_acquire_lock(
        self,
        *,
        lock_key: str,
        owner_id: DistributedLockOwnerId,
        ttl_seconds: float = 30.0,
        metadata: JsonMapping | None = None,
        now: datetime | None = None,
    ) -> DistributedLockLifecycleResult | None:
        if self._distributed_coordinator is None:
            return None
        return self._distributed_coordinator.acquire_lock(
            lock_key=lock_key,
            owner_id=owner_id,
            ttl_seconds=ttl_seconds,
            metadata=metadata,
            now=now,
        )

    def distributed_heartbeat_lock(
        self,
        lease_id: DistributedLockLeaseId,
        *,
        owner_id: DistributedLockOwnerId,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> DistributedLockLifecycleResult | None:
        if self._distributed_coordinator is None:
            return None
        return self._distributed_coordinator.heartbeat_lock(
            lease_id,
            owner_id=owner_id,
            ttl_seconds=ttl_seconds,
            now=now,
        )

    def distributed_release_lock(
        self,
        lease_id: DistributedLockLeaseId,
        *,
        owner_id: DistributedLockOwnerId,
        now: datetime | None = None,
    ) -> DistributedLockLifecycleResult | None:
        if self._distributed_coordinator is None:
            return None
        return self._distributed_coordinator.release_lock(
            lease_id,
            owner_id=owner_id,
            now=now,
        )

    def distributed_expire(
        self,
        *,
        now: datetime | None = None,
    ) -> DistributedMaintenanceResult | None:
        if self._distributed_coordinator is None:
            return None
        return self._distributed_coordinator.expire(now=now)

    def distributed_cancel_work_item(
        self,
        work_item_id: WorkItemId,
        *,
        reason: str = "distributed work item cancelled",
        now: datetime | None = None,
    ) -> DistributedCancellationResult | None:
        if self._distributed_coordinator is None:
            return None
        return self._distributed_coordinator.cancel_work_item(
            work_item_id,
            reason=reason,
            now=now,
        )

    async def distributed_run_worker_once(
        self,
        worker_id: WorkerId,
        *,
        lease_ttl_seconds: float = 30.0,
        worker_ttl_seconds: float = 30.0,
        heartbeat_interval_seconds: float | None = None,
    ) -> WorkerRunResult | None:
        if self._distributed_coordinator is None:
            return None
        worker = WorkQueueWorker(
            queue=self._distributed_coordinator.queue,
            worker_id=worker_id,
            handlers=self._distributed_work_handlers(),
            lease_ttl_seconds=lease_ttl_seconds,
            worker_registry=self._distributed_coordinator.workers,
            worker_ttl_seconds=worker_ttl_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )
        return await worker.run_once()

    async def distributed_run_worker_until_idle(
        self,
        worker_id: WorkerId,
        *,
        max_items: int,
        lease_ttl_seconds: float = 30.0,
        worker_ttl_seconds: float = 30.0,
        heartbeat_interval_seconds: float | None = None,
    ) -> tuple[WorkerRunResult, ...] | None:
        if self._distributed_coordinator is None:
            return None
        worker = WorkQueueWorker(
            queue=self._distributed_coordinator.queue,
            worker_id=worker_id,
            handlers=self._distributed_work_handlers(),
            lease_ttl_seconds=lease_ttl_seconds,
            worker_registry=self._distributed_coordinator.workers,
            worker_ttl_seconds=worker_ttl_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )
        return await worker.run_until_idle(max_items=max_items)

    def _distributed_work_handlers(self) -> Mapping[str, WorkHandler]:
        return {
            WorkKind.AGENT_SESSION.value: self._handle_distributed_session_work,
            WorkKind.AGENT_GOAL.value: self._handle_distributed_goal_work,
            WorkKind.TASK.value: self._handle_distributed_task_work,
            WorkKind.TOOL_ACTION.value: self._handle_distributed_action_work,
        }

    async def _handle_distributed_session_work(self, item: WorkItem) -> WorkHandlerResult:
        if item.session_id is None:
            return WorkHandlerResult.failed(
                "agent_session work item missing session_id", retry=False
            )
        try:
            session = await self.get_session(item.session_id)
        except StateNotFoundError as exc:
            return WorkHandlerResult.failed(f"session not found: {exc}", retry=False)
        if session.pending_action is not None:
            return WorkHandlerResult.failed(
                "session requires explicit confirmation before distributed resume",
                retry=False,
            )
        if session.goal_status in {
            GoalStatus.COMPLETED,
            GoalStatus.FAILED,
            GoalStatus.CANCELLED,
        }:
            return WorkHandlerResult.completed(
                f"session already terminal: {session.goal_status.value}"
            )
        if session.goal_status is not GoalStatus.WAITING:
            return WorkHandlerResult.failed(
                f"session is not resumable: {session.goal_status.value}",
                retry=True,
            )
        run = await self.resume_session(item.session_id)
        if run.result.status in {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.WAITING,
            ExecutionStatus.CANCELLED,
        }:
            return WorkHandlerResult.completed(
                f"distributed session resume settled as {run.result.status.value}"
            )
        return WorkHandlerResult.failed(
            f"distributed session resume failed: {run.result.reason}",
            retry=False,
        )

    async def _handle_distributed_goal_work(self, item: WorkItem) -> WorkHandlerResult:
        try:
            goal, task = _goal_task_from_work_payload(item.payload)
        except ValueError as exc:
            return WorkHandlerResult.failed(f"invalid agent_goal work payload: {exc}", retry=False)
        run = await self.run_goal(goal, task)
        if run.result.status is ExecutionStatus.COMPLETED:
            return WorkHandlerResult.completed(f"session completed: {run.result.session_id}")
        if run.result.status is ExecutionStatus.WAITING:
            return WorkHandlerResult.completed(f"session waiting: {run.result.session_id}")
        if run.result.status is ExecutionStatus.CANCELLED:
            return WorkHandlerResult.completed(f"session cancelled: {run.result.session_id}")
        return WorkHandlerResult.failed(
            f"distributed goal run failed: {run.result.reason}",
            retry=False,
        )

    async def _handle_distributed_task_work(self, item: WorkItem) -> WorkHandlerResult:
        if item.session_id is None:
            return WorkHandlerResult.failed("task work item missing session_id", retry=False)
        if item.task_id is None:
            return WorkHandlerResult.failed("task work item missing task_id", retry=False)
        try:
            session = await self.get_session(item.session_id)
        except StateNotFoundError as exc:
            return WorkHandlerResult.failed(f"session not found: {exc}", retry=False)
        if session.current_task_id != item.task_id:
            return WorkHandlerResult.failed(
                "task work item does not match current session task: "
                f"{item.task_id} != {session.current_task_id}",
                retry=False,
            )
        if session.pending_action is not None:
            return WorkHandlerResult.failed(
                "task requires explicit confirmation before distributed resume",
                retry=False,
            )
        if session.goal_status in {
            GoalStatus.COMPLETED,
            GoalStatus.FAILED,
            GoalStatus.CANCELLED,
        }:
            return WorkHandlerResult.completed(
                f"session already terminal: {session.goal_status.value}"
            )
        if session.goal_status is not GoalStatus.WAITING:
            return WorkHandlerResult.failed(
                f"session task is not resumable: {session.goal_status.value}",
                retry=True,
            )
        run = await self.resume_session(item.session_id)
        if run.result.status in {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.WAITING,
            ExecutionStatus.CANCELLED,
        }:
            return WorkHandlerResult.completed(
                f"distributed task resume settled as {run.result.status.value}"
            )
        return WorkHandlerResult.failed(
            f"distributed task resume failed: {run.result.reason}",
            retry=False,
        )

    async def _handle_distributed_action_work(self, item: WorkItem) -> WorkHandlerResult:
        if item.session_id is None:
            return WorkHandlerResult.failed("tool_action work item missing session_id", retry=False)
        if item.task_id is None:
            return WorkHandlerResult.failed("tool_action work item missing task_id", retry=False)
        if item.action_id is None:
            return WorkHandlerResult.failed("tool_action work item missing action_id", retry=False)
        if item.payload.get("confirmed") is not True:
            return WorkHandlerResult.failed(
                "tool_action work item requires confirmed=true",
                retry=False,
            )
        try:
            session = await self.get_session(item.session_id)
        except StateNotFoundError as exc:
            return WorkHandlerResult.failed(f"session not found: {exc}", retry=False)
        pending = session.pending_action
        if pending is None:
            return WorkHandlerResult.failed(
                "tool_action work item requires a pending action",
                retry=False,
            )
        if session.current_task_id != item.task_id:
            return WorkHandlerResult.failed(
                "tool_action work item does not match current session task: "
                f"{item.task_id} != {session.current_task_id}",
                retry=False,
            )
        if pending.action_id != item.action_id:
            return WorkHandlerResult.failed(
                "tool_action work item does not match pending action: "
                f"{item.action_id} != {pending.action_id}",
                retry=False,
            )
        if session.goal_status is not GoalStatus.WAITING:
            return WorkHandlerResult.failed(
                f"session action is not confirmable: {session.goal_status.value}",
                retry=True,
            )
        run = await self.resume_session(item.session_id, confirmed=True)
        if run.result.status in {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.WAITING,
            ExecutionStatus.CANCELLED,
        }:
            return WorkHandlerResult.completed(
                f"distributed action resume settled as {run.result.status.value}"
            )
        return WorkHandlerResult.failed(
            f"distributed action resume failed: {run.result.reason}",
            retry=False,
        )

    async def run_goal(self, goal: Goal, task: Task) -> RuntimeRun:
        return await self._runtime_api.run_goal(goal, task)

    async def resume_session(
        self,
        session_id: SessionId,
        *,
        confirmed: bool | None = None,
    ) -> RuntimeRun:
        return await self._runtime_api.resume_session(session_id, confirmed=confirmed)

    async def pause_session(
        self,
        session_id: SessionId,
        *,
        reason: str = "session paused",
    ) -> RuntimeRun:
        return await self._runtime_api.pause_session(session_id, reason=reason)

    async def cancel_session(
        self,
        session_id: SessionId,
        *,
        reason: str = "session cancelled",
    ) -> RuntimeRun:
        return await self._runtime_api.cancel_session(session_id, reason=reason)

    async def get_session(self, session_id: SessionId) -> SessionView:
        return await self._runtime_api.get_session(session_id)

    async def session_explorer(self, session_id: SessionId) -> SessionExplorerView:
        diagnostics = await self._runtime_api.get_session_diagnostics(session_id)
        return SessionExplorerView(
            diagnostics.session,
            diagnostics.evidence,
            self._world_fact_views(session_id, diagnostics.evidence),
        )

    async def list_sessions(
        self,
        *,
        after_session_id: SessionId | None = None,
        limit: int | None = None,
    ) -> tuple[SessionSummaryView, ...]:
        return await self._runtime_api.list_sessions(
            after_session_id=after_session_id,
            limit=limit,
        )

    async def stream_sessions(
        self,
        *,
        after_session_id: SessionId | None = None,
        limit: int | None = None,
    ) -> RuntimeSessionBatch:
        return await self._runtime_api.stream_sessions(
            after_session_id=after_session_id,
            limit=limit,
        )

    async def list_events(self, session_id: SessionId) -> tuple[RuntimeEventView, ...]:
        return await self._runtime_api.list_events(session_id)

    async def metrics(self) -> RuntimeMetricsView:
        sessions = await self.list_sessions()
        return build_runtime_metrics(sessions, await self._list_all_events(sessions))

    async def prometheus_metrics(self) -> str:
        return build_prometheus_metrics_export(await self.metrics())

    async def cost(self, session_id: SessionId | None = None) -> RuntimeCostView:
        if session_id is not None:
            return build_runtime_cost(await self.list_events(session_id))
        sessions = await self.list_sessions()
        return build_runtime_cost(await self._list_all_events(sessions))

    async def logs(
        self,
        session_id: SessionId | None = None,
    ) -> tuple[RuntimeLogRecordView, ...]:
        if session_id is not None:
            return build_runtime_logs(await self.list_events(session_id), session_id=session_id)
        sessions = await self.list_sessions()
        return build_runtime_logs(await self._list_all_events(sessions))

    async def traces(
        self,
        session_id: SessionId | None = None,
    ) -> tuple[RuntimeTraceSpanView, ...]:
        if session_id is not None:
            return build_runtime_trace_spans(
                await self.list_events(session_id),
                session_id=session_id,
            )
        sessions = await self.list_sessions()
        return build_runtime_trace_spans(await self._list_all_events(sessions))

    async def opentelemetry_traces(
        self,
        session_id: SessionId | None = None,
    ) -> JsonMapping:
        return build_opentelemetry_trace_export(await self.traces(session_id))

    async def doctor(self) -> DoctorReportView:
        health = self.health()
        ready = self.ready()
        config = self.config()
        sessions = await self.list_sessions()
        events = await self._list_all_events(sessions)
        distributed_health = self.distributed_health()
        distributed_snapshot = self.distributed_snapshot()
        return build_doctor_report(
            health_status=health.status,
            ready=ready.ready,
            ready_reason=ready.reason,
            domain_count=ready.domain_count,
            capability_count=ready.capability_count,
            tool_count=ready.tool_count,
            sessions=sessions,
            events=events,
            configured_domain_count=len(config.domains),
            store_backend=config.store_backend,
            max_iterations=config.max_iterations,
            max_recovery_steps=config.max_recovery_steps,
            distributed_health_status=None
            if distributed_health is None
            else distributed_health.status.value,
            distributed_health_check_count=None
            if distributed_health is None
            else len(distributed_health.checks),
            distributed_capacity_gap_count=None
            if distributed_health is None
            else len(distributed_health.capacity_gaps),
            distributed_expiring_lease_count=None
            if distributed_health is None
            else len(distributed_health.expiring_leases),
            distributed_invalid_session_work_item_count=None
            if distributed_snapshot is None
            else self._distributed_invalid_session_work_item_count(sessions, distributed_snapshot),
        )

    async def audit_records(
        self,
        session_id: SessionId | None = None,
    ) -> tuple[AuditRecordView, ...]:
        if session_id is not None:
            return build_audit_records(
                await self.list_events(session_id),
                session_id=session_id,
            )
        sessions = await self.list_sessions()
        return build_audit_records(await self._list_all_events(sessions))

    async def stream_events(
        self,
        session_id: SessionId,
        *,
        after_event_id: EventId | None = None,
        limit: int | None = None,
    ) -> RuntimeEventBatch:
        return await self._runtime_api.stream_events(
            session_id,
            after_event_id=after_event_id,
            limit=limit,
        )

    async def _list_all_events(
        self,
        sessions: tuple[SessionSummaryView, ...],
    ) -> tuple[RuntimeEventView, ...]:
        events = list(await self._runtime_api.list_all_events())
        if events:
            return tuple(sorted(events, key=lambda event: event.occurred_at))
        if not sessions:
            return ()
        events = []
        for session in sessions:
            events.extend(await self.list_events(session.session_id))
        return tuple(sorted(events, key=lambda event: event.occurred_at))

    def _distributed_invalid_session_work_item_count(
        self,
        sessions: tuple[SessionSummaryView, ...],
        snapshot: DistributedRuntimeSnapshot,
    ) -> int:
        current_task_by_session = {
            session.session_id: session.current_task_id for session in sessions
        }
        invalid_count = 0
        for item in snapshot.work_queue.items:
            if item.kind == WorkKind.AGENT_SESSION.value:
                if item.session_id is None or item.session_id not in current_task_by_session:
                    invalid_count += 1
                continue
            if item.kind not in {WorkKind.TASK.value, WorkKind.TOOL_ACTION.value}:
                continue
            if item.session_id is None:
                invalid_count += 1
                continue
            expected_task_id = current_task_by_session.get(item.session_id)
            if expected_task_id is None or item.task_id is None or item.task_id != expected_task_id:
                invalid_count += 1
                continue
            if item.kind == WorkKind.TOOL_ACTION.value:
                session = next(
                    (
                        candidate
                        for candidate in sessions
                        if candidate.session_id == item.session_id
                    ),
                    None,
                )
                if item.action_id is None or session is None or not session.pending_action:
                    invalid_count += 1
        return invalid_count

    def _world_fact_views(
        self,
        session_id: SessionId,
        evidence: tuple[EvidenceView, ...],
    ) -> tuple[WorldFactView, ...]:
        if not evidence or not self._components.world_updaters:
            return ()
        world_model = InMemoryWorldModel()
        world_model.rebuild(
            session_id,
            tuple(_evidence_from_view(item) for item in evidence),
            self._components.world_updaters,
        )
        return tuple(world_fact_view(item) for item in world_model.snapshot(session_id).facts)


def domain_view(domain: ActiveDomain, *, primary: bool) -> DomainView:
    metadata = domain.manifest.metadata
    return DomainView(
        name=metadata.name,
        version=metadata.version,
        description=metadata.description,
        primary=primary,
        ontology=domain.manifest.ontology,
        capability_names=domain.manifest.capability_names,
        evaluator_names=domain.manifest.evaluator_names,
    )


def domain_package_view(package: DomainPackage) -> DomainPackageView:
    manifest = package.manifest
    return DomainPackageView(
        name=manifest.name,
        version=manifest.version,
        description=manifest.description,
        author=manifest.author,
        entrypoint=manifest.entrypoint,
        tags=manifest.tags,
        ontology=manifest.ontology,
        capability_names=manifest.capabilities,
        tool_names=manifest.tools,
        policy_names=manifest.policies,
        procedure_names=manifest.procedures,
        knowledge_names=manifest.knowledge,
        evaluator_names=manifest.evaluators,
        context_provider_names=manifest.context_providers,
        prompt_names=manifest.prompts,
        dependencies=manifest.dependencies,
        required_tools=manifest.required_tools,
        runtime_api_compatibility=manifest.compatibility.runtime_api,
        domain_api_compatibility=manifest.compatibility.domain_api,
        security=manifest.security,
        root_path=str(package.root_path),
        manifest_path=str(package.manifest_path),
    )


def profile_view(profile: AgentProfile) -> ProfileView:
    assert profile.domain.name is not None
    assert profile.domain.version is not None
    return ProfileView(
        name=profile.name,
        version=profile.version,
        description=profile.description,
        domain_name=profile.domain.name,
        domain_version=profile.domain.version,
        domains=tuple(domain.identity() for domain in profile.configured_domains()),
    )


def policy_view(policy: Policy, domain: ActiveDomain) -> PolicyView:
    if isinstance(policy, PolicyRule):
        return PolicyView(
            name=policy.name,
            description=policy.reason,
            policy_type=type(policy).__name__,
            effect=policy.effect,
            capability_names=policy.capabilities,
            categories=policy.categories,
            risks=policy.risks,
            domain_name=domain.identity.name,
            domain_version=domain.identity.version,
        )
    description = getattr(policy, "description", "")
    if not isinstance(description, str):
        description = ""
    return PolicyView(
        name=policy.name,
        description=description,
        policy_type=type(policy).__name__,
        effect=None,
        capability_names=(),
        categories=(),
        risks=(),
        domain_name=domain.identity.name,
        domain_version=domain.identity.version,
    )


def evaluator_view(evaluator: object, domain: ActiveDomain) -> EvaluatorView:
    name = getattr(evaluator, "name", "")
    return EvaluatorView(
        name=name if isinstance(name, str) else "",
        evaluator_type=type(evaluator).__name__,
        domain_name=domain.identity.name,
        domain_version=domain.identity.version,
    )


def memory_view(record: MemoryRecord) -> MemoryView:
    return MemoryView(
        memory_id=str(record.id),
        kind=record.kind,
        subject=record.subject,
        content=record.content,
        scope=record.scope,
        confidence=record.confidence,
        source_session_id=record.source_session_id,
        created_at=record.created_at,
    )


def _format_identities(identities: tuple[DomainIdentity, ...]) -> str:
    return ", ".join(f"{identity.name}@{identity.version}" for identity in identities) or "<none>"


def runtime_config_domain_views(
    identities: tuple[DomainIdentity, ...],
) -> tuple[RuntimeConfigDomainView, ...]:
    return tuple(
        RuntimeConfigDomainView(identity.name, identity.version, index == 0)
        for index, identity in enumerate(identities)
    )


def world_fact_view(fact: WorldFact) -> WorldFactView:
    return WorldFactView(
        fact.subject,
        fact.claim,
        _copy_json_value(fact.value),
        fact.confidence,
        fact.observed_at,
        tuple(str(item) for item in fact.evidence_ids),
    )


def _evidence_from_view(view: EvidenceView) -> Evidence:
    return Evidence(
        view.session_id,
        view.task_id,
        view.action_id,
        view.observation_id,
        view.subject,
        view.claim,
        _copy_json_value(view.value),
        view.source,
        view.confidence,
        view.evidence_id,
        view.observed_at,
    )


def _copy_json_value(value: JsonValue) -> JsonValue:
    if isinstance(value, list):
        return [_copy_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _copy_json_value(item) for key, item in value.items()}
    return value


def _goal_work_payload(goal: Goal, task: Task) -> JsonMapping:
    criteria: list[JsonValue] = []
    for criterion in goal.success_criteria:
        criteria.append(
            {
                "key": criterion.key,
                "expected": _copy_json_value(criterion.expected),
            }
        )
    payload: dict[str, JsonValue] = {
        "goal": {
            "id": str(goal.id),
            "description": goal.description,
            "success_criteria": criteria,
            "created_at": goal.created_at.isoformat(),
        },
        "task": {
            "id": str(task.id),
            "description": task.description,
            "required_criteria": list(task.required_criteria),
            "created_at": task.created_at.isoformat(),
        },
    }
    return immutable_json(payload)


def _goal_work_idempotency_key(goal: Goal, task: Task) -> str:
    return f"goal:{goal.id}:{task.id}"


def _goal_task_from_work_payload(payload: Mapping[str, JsonValue]) -> tuple[Goal, Task]:
    goal_payload = _object_payload_field(payload, "goal", "goal")
    task_payload = _object_payload_field(payload, "task", "task")
    return (
        Goal(
            _required_string_payload_field(goal_payload, "description", "goal.description"),
            _success_criteria_from_payload(goal_payload),
            id=GoalId(_required_string_payload_field(goal_payload, "id", "goal.id")),
            created_at=_datetime_payload_field(goal_payload, "created_at", "goal.created_at"),
        ),
        Task(
            _required_string_payload_field(task_payload, "description", "task.description"),
            _string_tuple_payload_field(
                task_payload,
                "required_criteria",
                "task.required_criteria",
            ),
            id=TaskId(_required_string_payload_field(task_payload, "id", "task.id")),
            created_at=_datetime_payload_field(task_payload, "created_at", "task.created_at"),
        ),
    )


def _object_payload_field(
    payload: Mapping[str, JsonValue],
    key: str,
    field: str,
) -> Mapping[str, JsonValue]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return cast(Mapping[str, JsonValue], value)


def _success_criteria_from_payload(
    payload: Mapping[str, JsonValue],
) -> tuple[SuccessCriterion, ...]:
    value = payload.get("success_criteria")
    if not isinstance(value, list):
        raise ValueError("goal.success_criteria must be a list")
    if not value:
        raise ValueError("goal.success_criteria must not be empty")
    criteria: list[SuccessCriterion] = []
    for index, item in enumerate(value):
        field = f"goal.success_criteria[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{field} must be an object")
        item_payload = cast(Mapping[str, JsonValue], item)
        criteria.append(
            SuccessCriterion(
                _required_string_payload_field(item_payload, "key", f"{field}.key"),
                _required_json_payload_field(item_payload, "expected", f"{field}.expected"),
            )
        )
    return tuple(criteria)


def _string_tuple_payload_field(
    payload: Mapping[str, JsonValue],
    key: str,
    field: str,
) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    values: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{field}[{index}] must be a string")
        if not item.strip():
            raise ValueError(f"{field}[{index}] must not be empty")
        values.append(item)
    return tuple(values)


def _required_string_payload_field(
    payload: Mapping[str, JsonValue],
    key: str,
    field: str,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value


def _required_json_payload_field(
    payload: Mapping[str, JsonValue],
    key: str,
    field: str,
) -> JsonValue:
    if key not in payload:
        raise ValueError(f"{field} is required")
    return _copy_json_value(payload[key])


def _datetime_payload_field(
    payload: Mapping[str, JsonValue],
    key: str,
    field: str,
) -> datetime:
    value = payload.get(key)
    if value is None:
        return utc_now()
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO datetime string")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO datetime string") from exc


def _not_ready_reason(
    *,
    has_domains: bool,
    has_capabilities: bool,
    has_tools: bool,
    missing_tools: tuple[str, ...],
) -> str:
    if not has_domains:
        return "no domains loaded"
    if not has_capabilities:
        return "no capabilities registered"
    if not has_tools:
        return "no tools registered"
    if missing_tools:
        return "capabilities without tools: " + ", ".join(missing_tools)
    return "not ready"
