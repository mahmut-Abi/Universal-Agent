from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from universal_agent.core import (
    ActionId,
    EventId,
    Goal,
    JsonMapping,
    SessionId,
    Task,
    TaskId,
    immutable_json,
)
from universal_agent.distributed import (
    DistributedCancellationResult,
    DistributedHealthReport,
    DistributedLockLeaseId,
    DistributedLockLifecycleResult,
    DistributedLockOwnerId,
    DistributedMaintenanceResult,
    DistributedPruneResult,
    DistributedRuntimeCoordinator,
    DistributedRuntimeSnapshot,
    DistributedSchedulingResult,
    DistributedWorkerLifecycleResult,
    WorkerId,
    WorkerRunResult,
    WorkItemId,
)
from universal_agent.domain import (
    DomainPackageRegistry,
    DomainPackageVerificationReport,
    RuntimeComponents,
)
from universal_agent.multi_agent import (
    AgentDelegationState,
    AgentRegistry,
)
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
from universal_agent.security import SecretResolutionReport
from universal_agent.service.catalog import (
    capability_views,
    domain_package_detail,
    domain_package_views,
    domain_views,
    evaluator_views,
    memory_views,
    multi_agent_view,
    policy_views,
    profile_views,
    tool_views,
)
from universal_agent.service.config_views import (
    format_identities,
    not_ready_reason,
    redact_environment,
    runtime_config_domain_views,
    runtime_model_config_view,
    runtime_secret_ref_views,
    secret_readiness_failure,
    secret_scan_payload,
)
from universal_agent.service.distributed_runtime import DistributedRuntimeController
from universal_agent.service.projections import (
    evidence_from_view,
    profile_view,
    world_neighborhood_view,
    world_projection_views_from_snapshot,
)
from universal_agent.service.state_event_repair import (
    missing_terminal_state_events,
    planned_state_event_repair_view,
    state_event_repair_view,
    unrepairable_state_event_items,
)
from universal_agent.service.views import (
    CapabilityView,
    DistributedPendingActionSchedulingResult,
    DomainPackageView,
    DomainView,
    EvaluatorView,
    HealthView,
    MemoryView,
    MultiAgentView,
    PolicyView,
    ProfileView,
    ReadyView,
    RuntimeConfigView,
    SessionExplorerView,
    SessionWorldView,
    StateEventRepairReport,
    ToolView,
    WorldEntityView,
    WorldFactView,
    WorldRelationView,
)
from universal_agent.world import (
    InMemoryWorldModel,
    WorldSnapshot,
)

if TYPE_CHECKING:
    from universal_agent.host.config import RuntimeConfig


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
        secret_resolution: SecretResolutionReport | None = None,
        distributed_coordinator: DistributedRuntimeCoordinator | None = None,
        domain_packages: DomainPackageRegistry | None = None,
        agent_registry: AgentRegistry | None = None,
        agent_delegation_state: AgentDelegationState | None = None,
    ) -> None:
        self._runtime_api = runtime_api
        self._components = components
        self._profiles = ProfileRegistry(profiles)
        self._config = config
        self._secret_resolution = secret_resolution
        self._distributed_coordinator = distributed_coordinator
        self._domain_packages = domain_packages or DomainPackageRegistry()
        self._agent_registry = agent_registry
        self._agent_delegation_state = agent_delegation_state or AgentDelegationState()
        self._distributed_runtime = DistributedRuntimeController(
            runtime_api=runtime_api,
            coordinator=distributed_coordinator,
            config=config,
        )

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
        catalog_ready = bool(domains) and bool(capabilities) and bool(tools) and not missing_tools
        secret_failure = secret_readiness_failure(self._secret_resolution)
        ready = catalog_ready and secret_failure is None
        reason = "ready"
        if not catalog_ready:
            reason = not_ready_reason(
                has_domains=bool(domains),
                has_capabilities=bool(capabilities),
                has_tools=bool(tools),
                missing_tools=missing_tools,
            )
        elif secret_failure is not None:
            reason = secret_failure
        return ReadyView(
            ready=ready,
            reason=reason,
            domain_count=len(domains),
            capability_count=len(capabilities),
            tool_count=len(tools),
        )

    def domains(self) -> tuple[DomainView, ...]:
        return domain_views(self._components)

    def domain_packages(self, *, tag: str | None = None) -> tuple[DomainPackageView, ...]:
        return domain_package_views(self._domain_packages, tag=tag)

    def domain_package(self, name: str, version: str | None = None) -> DomainPackageView:
        return domain_package_detail(self._domain_packages, name, version)

    def domain_package_verification(
        self,
        *,
        verify_paths: bool = False,
    ) -> DomainPackageVerificationReport:
        return self._domain_packages.verify(verify_paths=verify_paths)

    def capabilities(self) -> tuple[CapabilityView, ...]:
        return capability_views(self._components)

    def tools(self) -> tuple[ToolView, ...]:
        return tool_views(self._components)

    def policies(self) -> tuple[PolicyView, ...]:
        return policy_views(self._components)

    def evaluators(self) -> tuple[EvaluatorView, ...]:
        return evaluator_views(self._components)

    def memories(self) -> tuple[MemoryView, ...]:
        return memory_views(self._components)

    def profiles(self) -> tuple[ProfileView, ...]:
        return profile_views(self._profiles)

    def multi_agent(self) -> MultiAgentView:
        return multi_agent_view(self._agent_registry, self._agent_delegation_state)

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
                f"{format_identities(profile_domains)} do not match active runtime domains "
                f"{format_identities(active_domains)}"
            )
        return None

    def config(self) -> RuntimeConfigView:
        identities = self._components.domain_composition.identities
        state_event_commit = self._runtime_api.state_event_commit()
        if self._config is None:
            return RuntimeConfigView(
                environment=immutable_json(),
                domain_package_paths=(),
                secrets=(),
                store_backend="memory",
                store_path=None,
                distributed_queue_backend="memory",
                distributed_queue_path=None,
                distributed_locks_backend="memory",
                distributed_locks_path=None,
                distributed_workers_backend="memory",
                distributed_workers_path=None,
                max_iterations=20,
                max_recovery_steps=8,
                domains=runtime_config_domain_views(identities),
                distributed_terminal_retention_seconds=None,
                state_event_commit_supported=state_event_commit.supported,
                state_event_commit_strategy=state_event_commit.strategy,
                state_event_commit_shared_store=state_event_commit.shared_store,
            )
        return RuntimeConfigView(
            environment=redact_environment(self._config.environment),
            domain_package_paths=self._config.domain_package_paths,
            model=runtime_model_config_view(self._config.model),
            secrets=runtime_secret_ref_views(
                self._config.secrets,
                self._secret_resolution,
            ),
            store_backend=self._config.store.backend.value,
            store_path=self._config.store.path,
            distributed_queue_backend=self._config.distributed_queue.backend.value,
            distributed_queue_path=self._config.distributed_queue.path,
            distributed_locks_backend=self._config.distributed_locks.backend.value,
            distributed_locks_path=self._config.distributed_locks.path,
            distributed_workers_backend=self._config.distributed_workers.backend.value,
            distributed_workers_path=self._config.distributed_workers.path,
            max_iterations=self._config.limits.max_iterations,
            max_recovery_steps=self._config.limits.max_recovery_steps,
            domains=runtime_config_domain_views(
                identities,
                self._config.configured_domains(),
            ),
            distributed_terminal_retention_seconds=(
                self._config.distributed_terminal_retention_seconds
            ),
            state_event_commit_supported=state_event_commit.supported,
            state_event_commit_strategy=state_event_commit.strategy,
            state_event_commit_shared_store=state_event_commit.shared_store,
        )

    def distributed_snapshot(self) -> DistributedRuntimeSnapshot | None:
        return self._distributed_runtime.snapshot()

    def distributed_health(self, *, now: datetime | None = None) -> DistributedHealthReport | None:
        return self._distributed_runtime.health(now=now)

    def distributed_schedule_session(
        self,
        session_id: SessionId,
        *,
        payload: JsonMapping | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> DistributedSchedulingResult | None:
        return self._distributed_runtime.schedule_session(
            session_id,
            payload=payload,
            priority=priority,
            max_attempts=max_attempts,
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
        return self._distributed_runtime.schedule_goal(
            goal,
            task,
            priority=priority,
            max_attempts=max_attempts,
            idempotency_key=idempotency_key,
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
        return self._distributed_runtime.schedule_task(
            session_id,
            task_id,
            payload=payload,
            priority=priority,
            max_attempts=max_attempts,
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
        return self._distributed_runtime.schedule_action(
            session_id,
            task_id,
            action_id,
            confirmed=confirmed,
            priority=priority,
            max_attempts=max_attempts,
            now=now,
        )

    async def distributed_schedule_pending_actions(
        self,
        *,
        confirmed: bool,
        priority: int = 0,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> DistributedPendingActionSchedulingResult | None:
        return await self._distributed_runtime.schedule_pending_actions(
            confirmed=confirmed,
            priority=priority,
            max_attempts=max_attempts,
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
        return self._distributed_runtime.register_worker(
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
        return self._distributed_runtime.heartbeat_worker(
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
        return self._distributed_runtime.drain_worker(
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
        return self._distributed_runtime.mark_worker_offline(
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
        return self._distributed_runtime.acquire_lock(
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
        return self._distributed_runtime.heartbeat_lock(
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
        return self._distributed_runtime.release_lock(
            lease_id,
            owner_id=owner_id,
            now=now,
        )

    def distributed_expire(
        self,
        *,
        now: datetime | None = None,
    ) -> DistributedMaintenanceResult | None:
        return self._distributed_runtime.expire(now=now)

    def distributed_prune_terminal_work_items(
        self,
        *,
        before: datetime | None = None,
        now: datetime | None = None,
    ) -> DistributedPruneResult | None:
        return self._distributed_runtime.prune_terminal_work_items(before=before, now=now)

    def distributed_cancel_work_item(
        self,
        work_item_id: WorkItemId,
        *,
        reason: str = "distributed work item cancelled",
        now: datetime | None = None,
    ) -> DistributedCancellationResult | None:
        return self._distributed_runtime.cancel_work_item(
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
        return await self._distributed_runtime.run_worker_once(
            worker_id,
            lease_ttl_seconds=lease_ttl_seconds,
            worker_ttl_seconds=worker_ttl_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )

    async def distributed_run_worker_until_idle(
        self,
        worker_id: WorkerId,
        *,
        max_items: int,
        lease_ttl_seconds: float = 30.0,
        worker_ttl_seconds: float = 30.0,
        heartbeat_interval_seconds: float | None = None,
    ) -> tuple[WorkerRunResult, ...] | None:
        return await self._distributed_runtime.run_worker_until_idle(
            worker_id,
            max_items=max_items,
            lease_ttl_seconds=lease_ttl_seconds,
            worker_ttl_seconds=worker_ttl_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
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
        (
            world_facts,
            world_fact_histories,
            world_entities,
            world_relations,
        ) = world_projection_views_from_snapshot(
            self._world_snapshot(session_id, diagnostics.evidence)
        )
        return SessionExplorerView(
            diagnostics.session,
            diagnostics.evidence,
            world_facts,
            world_entities,
            world_relations,
            world_fact_histories,
        )

    async def session_world(
        self,
        session_id: SessionId,
        *,
        entity_id: str | None = None,
        relation: str | None = None,
    ) -> SessionWorldView:
        if relation is not None and entity_id is None:
            raise ValueError("world relation filter requires entity_id")
        diagnostics = await self._runtime_api.get_session_diagnostics(session_id)
        snapshot = self._world_snapshot(session_id, diagnostics.evidence)
        (
            world_facts,
            world_fact_histories,
            world_entities,
            world_relations,
        ) = world_projection_views_from_snapshot(snapshot)
        neighborhood = (
            None
            if entity_id is None
            else world_neighborhood_view(snapshot.neighborhood_for(entity_id, relation=relation))
        )
        return SessionWorldView(
            diagnostics.session.session_id,
            world_facts,
            world_fact_histories,
            world_entities,
            world_relations,
            neighborhood,
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
            store_path=config.store_path,
            distributed_queue_backend=config.distributed_queue_backend,
            distributed_queue_path=config.distributed_queue_path,
            distributed_locks_backend=config.distributed_locks_backend,
            distributed_locks_path=config.distributed_locks_path,
            distributed_workers_backend=config.distributed_workers_backend,
            distributed_workers_path=config.distributed_workers_path,
            max_iterations=config.max_iterations,
            max_recovery_steps=config.max_recovery_steps,
            state_event_commit_supported=config.state_event_commit_supported,
            state_event_commit_strategy=config.state_event_commit_strategy,
            state_event_commit_shared_store=config.state_event_commit_shared_store,
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
            distributed_recommendation_count=None
            if distributed_health is None
            else len(distributed_health.recommendations),
            distributed_invalid_session_work_item_count=None
            if distributed_snapshot is None
            else await self._distributed_runtime.invalid_session_work_item_count(
                sessions,
                distributed_snapshot,
            ),
            distributed_terminal_work_item_count=None
            if distributed_snapshot is None
            else self._distributed_runtime.terminal_work_item_count(distributed_snapshot),
            secret_resolution=self._secret_resolution,
            secret_scan_payload=secret_scan_payload(config, events),
        )

    async def repair_state_event_consistency(
        self,
        *,
        confirmed: bool = False,
        dry_run: bool = False,
    ) -> StateEventRepairReport:
        if confirmed and dry_run:
            raise ValueError("state/event consistency repair cannot be confirmed and dry-run")
        if not confirmed and not dry_run:
            raise ValueError(
                "state/event consistency repair requires confirmed=true or dry_run=true"
            )

        sessions = await self.list_sessions()
        events = await self._list_all_events(sessions)
        skipped = unrepairable_state_event_items(sessions, events)
        if skipped:
            return StateEventRepairReport("blocked", (), skipped)

        repair_events = missing_terminal_state_events(sessions, events)
        if not repair_events:
            return StateEventRepairReport("clean", (), ())

        if dry_run:
            return StateEventRepairReport(
                "planned",
                tuple(planned_state_event_repair_view(event) for event in repair_events),
                (),
            )

        repaired = await self._runtime_api.record_repair_events(repair_events)
        return StateEventRepairReport(
            "repaired",
            tuple(state_event_repair_view(event) for event in repaired),
            (),
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

    def _world_projection_views(
        self,
        session_id: SessionId,
        evidence: tuple[EvidenceView, ...],
    ) -> tuple[
        tuple[WorldFactView, ...], tuple[WorldEntityView, ...], tuple[WorldRelationView, ...]
    ]:
        world_facts, _, world_entities, world_relations = world_projection_views_from_snapshot(
            self._world_snapshot(session_id, evidence)
        )
        return world_facts, world_entities, world_relations

    def _world_snapshot(
        self,
        session_id: SessionId,
        evidence: tuple[EvidenceView, ...],
    ) -> WorldSnapshot:
        if not self._components.world_updaters:
            return WorldSnapshot(session_id)
        world_model = InMemoryWorldModel()
        world_model.rebuild(
            session_id,
            tuple(evidence_from_view(item) for item in evidence),
            self._components.world_updaters,
        )
        return world_model.snapshot(session_id)
