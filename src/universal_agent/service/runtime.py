from __future__ import annotations

from collections.abc import AsyncIterator
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
from universal_agent.memory import (
    MemoryId,
    MemoryKind,
    MemoryNotFoundError,
    MemoryRecord,
)
from universal_agent.multi_agent import (
    AgentDelegationState,
    AgentRegistry,
)
from universal_agent.operations import (
    AuditIntegrityReportView,
    AuditRecordView,
    DoctorReportView,
    RuntimeCostView,
    RuntimeLogRecordView,
    RuntimeMetricsView,
    RuntimeTraceSpanView,
)
from universal_agent.profile import AgentProfile, ProfileRegistry
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
from universal_agent.service.catalog_service import CatalogService
from universal_agent.service.distributed import DistributedService
from universal_agent.service.distributed_runtime import DistributedRuntimeController
from universal_agent.service.operations import OperationsService
from universal_agent.service.projections import build_world_snapshot, memory_view
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
    WorldFactHistoryView,
    WorldFactView,
    WorldNeighborhoodView,
    WorldRelationView,
)
from universal_agent.world import WorldSnapshot

if TYPE_CHECKING:
    from universal_agent.host.config import RuntimeConfig


class RuntimeService:
    """Application-facing service module for future agentd adapters.

    Execution and lifecycle control flow through RuntimeAPI. This service adds
    product-level health, readiness, catalog, distributed, session and operations
    metadata. Those surfaces live in `CatalogService`, `DistributedService` and
    `OperationsService`; this class wires them and keeps the public method names
    stable for agentd, the CLI and the SDK.
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
        self._distributed_runtime = DistributedRuntimeController(
            runtime_api=runtime_api,
            coordinator=distributed_coordinator,
            config=config,
        )
        self._catalog = CatalogService(
            components=components,
            profiles=ProfileRegistry(profiles),
            domain_packages=domain_packages or DomainPackageRegistry(),
            config=config,
            secret_resolution=secret_resolution,
            runtime_api=runtime_api,
            agent_registry=agent_registry,
            agent_delegation_state=agent_delegation_state or AgentDelegationState(),
        )
        self._operations = OperationsService(
            runtime_api=runtime_api,
            components=components,
            distributed_runtime=self._distributed_runtime,
            secret_resolution=secret_resolution,
            config=config,
            catalog=self._catalog,
        )
        self._distributed = DistributedService(self._distributed_runtime)

    # ---- Catalog / metadata ----

    def health(self) -> HealthView:
        return self._catalog.health()

    def ready(self) -> ReadyView:
        return self._catalog.ready()

    def domains(self) -> tuple[DomainView, ...]:
        return self._catalog.domains()

    def domain_packages(self, *, tag: str | None = None) -> tuple[DomainPackageView, ...]:
        return self._catalog.domain_packages(tag=tag)

    def domain_package(self, name: str, version: str | None = None) -> DomainPackageView:
        return self._catalog.domain_package(name, version)

    def domain_package_verification(
        self, *, verify_paths: bool = False
    ) -> DomainPackageVerificationReport:
        return self._catalog.domain_package_verification(verify_paths=verify_paths)

    def capabilities(self) -> tuple[CapabilityView, ...]:
        return self._catalog.capabilities()

    def tools(self) -> tuple[ToolView, ...]:
        return self._catalog.tools()

    def policies(self) -> tuple[PolicyView, ...]:
        return self._catalog.policies()

    def evaluators(self) -> tuple[EvaluatorView, ...]:
        return self._catalog.evaluators()

    def memories(self) -> tuple[MemoryView, ...]:
        return self._catalog.memories()

    def create_memory(
        self,
        *,
        kind: MemoryKind,
        subject: str,
        content: str,
        scope: str = "",
        confidence: float = 1.0,
    ) -> MemoryView:
        """Create an operator-managed memory record in the runtime memory store."""

        record = MemoryRecord(
            kind=kind,
            subject=subject,
            content=content,
            scope=scope,
            confidence=confidence,
            source="user",
        )
        self._components.memory_store.add(record)
        return memory_view(record)

    def get_memory(self, memory_id: str) -> MemoryView | None:
        record = self._components.memory_store.get(MemoryId(memory_id))
        return memory_view(record) if record is not None else None

    def delete_memory(self, memory_id: str) -> bool:
        return self._components.memory_store.delete(MemoryId(memory_id))

    def require_memory(self, memory_id: str) -> MemoryView:
        view = self.get_memory(memory_id)
        if view is None:
            raise MemoryNotFoundError(f"memory record not found: {memory_id}")
        return view

    def profiles(self) -> tuple[ProfileView, ...]:
        return self._catalog.profiles()

    def multi_agent(self) -> MultiAgentView:
        return self._catalog.multi_agent()

    def profile(self, name: str) -> ProfileView:
        return self._catalog.profile(name)

    def accepts_profile(self, name: str) -> bool:
        return self._catalog.accepts_profile(name)

    def profile_selection_error(self, name: str) -> str | None:
        return self._catalog.profile_selection_error(name)

    def config(self) -> RuntimeConfigView:
        return self._catalog.config()

    # ---- Distributed ----

    def distributed_snapshot(self) -> DistributedRuntimeSnapshot | None:
        return self._distributed.snapshot()

    def distributed_health(self, *, now: datetime | None = None) -> DistributedHealthReport | None:
        return self._distributed.health(now=now)

    def distributed_schedule_session(
        self,
        session_id: SessionId,
        *,
        payload: JsonMapping | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        now: datetime | None = None,
    ) -> DistributedSchedulingResult | None:
        return self._distributed.schedule_session(
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
        return self._distributed.schedule_goal(
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
        return self._distributed.schedule_task(
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
        return self._distributed.schedule_action(
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
        return await self._distributed.schedule_pending_actions(
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
        return self._distributed.register_worker(
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
        return self._distributed.heartbeat_worker(worker_id, ttl_seconds=ttl_seconds, now=now)

    def distributed_drain_worker(
        self,
        worker_id: WorkerId,
        *,
        reason: str = "worker draining",
        now: datetime | None = None,
    ) -> DistributedWorkerLifecycleResult | None:
        return self._distributed.drain_worker(worker_id, reason=reason, now=now)

    def distributed_mark_worker_offline(
        self,
        worker_id: WorkerId,
        *,
        reason: str = "worker offline",
        now: datetime | None = None,
    ) -> DistributedWorkerLifecycleResult | None:
        return self._distributed.mark_worker_offline(worker_id, reason=reason, now=now)

    def distributed_acquire_lock(
        self,
        *,
        lock_key: str,
        owner_id: DistributedLockOwnerId,
        ttl_seconds: float = 30.0,
        metadata: JsonMapping | None = None,
        now: datetime | None = None,
    ) -> DistributedLockLifecycleResult | None:
        return self._distributed.acquire_lock(
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
        return self._distributed.heartbeat_lock(
            lease_id, owner_id=owner_id, ttl_seconds=ttl_seconds, now=now
        )

    def distributed_release_lock(
        self,
        lease_id: DistributedLockLeaseId,
        *,
        owner_id: DistributedLockOwnerId,
        now: datetime | None = None,
    ) -> DistributedLockLifecycleResult | None:
        return self._distributed.release_lock(lease_id, owner_id=owner_id, now=now)

    def distributed_expire(
        self, *, now: datetime | None = None
    ) -> DistributedMaintenanceResult | None:
        return self._distributed.expire(now=now)

    def distributed_prune_terminal_work_items(
        self,
        *,
        before: datetime | None = None,
        now: datetime | None = None,
    ) -> DistributedPruneResult | None:
        return self._distributed.prune_terminal_work_items(before=before, now=now)

    def distributed_cancel_work_item(
        self,
        work_item_id: WorkItemId,
        *,
        reason: str = "distributed work item cancelled",
        now: datetime | None = None,
    ) -> DistributedCancellationResult | None:
        return self._distributed.cancel_work_item(work_item_id, reason=reason, now=now)

    async def distributed_run_worker_once(
        self,
        worker_id: WorkerId,
        *,
        lease_ttl_seconds: float = 30.0,
        worker_ttl_seconds: float = 30.0,
        heartbeat_interval_seconds: float | None = None,
    ) -> WorkerRunResult | None:
        return await self._distributed.run_worker_once(
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
        return await self._distributed.run_worker_until_idle(
            worker_id,
            max_items=max_items,
            lease_ttl_seconds=lease_ttl_seconds,
            worker_ttl_seconds=worker_ttl_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
        )

    # ---- Execution / lifecycle / sessions / events ----

    async def run_goal(
        self,
        goal: Goal,
        task: Task,
        *,
        initial_state: JsonMapping | None = None,
    ) -> RuntimeRun:
        return await self._runtime_api.run_goal(goal, task, initial_state=initial_state)

    async def run_compiled_goal(
        self,
        goal: Goal,
        *,
        initial_state: JsonMapping | None = None,
    ) -> RuntimeRun:
        return await self._runtime_api.run_compiled_goal(goal, initial_state=initial_state)

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
        ) = self._world_projection_views(session_id, diagnostics.evidence)
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
        ) = self._world_projection_views(session_id, diagnostics.evidence)
        neighborhood = (
            None if entity_id is None else self._world_neighborhood(snapshot, entity_id, relation)
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

    async def watch_events(
        self,
        session_id: SessionId,
        *,
        after_event_id: EventId | None = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncIterator[RuntimeEventView]:
        """Yield events as they arrive for real-time SSE streaming."""
        async for view in self._runtime_api.watch_events(
            session_id,
            after_event_id=after_event_id,
            heartbeat_interval=heartbeat_interval,
        ):
            yield view

    # ---- Operations ----

    async def metrics(self) -> RuntimeMetricsView:
        return await self._operations.metrics()

    async def prometheus_metrics(self) -> str:
        return await self._operations.prometheus_metrics()

    async def cost(self, session_id: SessionId | None = None) -> RuntimeCostView:
        return await self._operations.cost(session_id)

    async def logs(self, session_id: SessionId | None = None) -> tuple[RuntimeLogRecordView, ...]:
        return await self._operations.logs(session_id)

    async def traces(self, session_id: SessionId | None = None) -> tuple[RuntimeTraceSpanView, ...]:
        return await self._operations.traces(session_id)

    async def opentelemetry_traces(self, session_id: SessionId | None = None) -> JsonMapping:
        return await self._operations.opentelemetry_traces(session_id)

    async def doctor(self) -> DoctorReportView:
        return await self._operations.doctor()

    async def repair_state_event_consistency(
        self,
        *,
        confirmed: bool = False,
        dry_run: bool = False,
    ) -> StateEventRepairReport:
        return await self._operations.repair_state_event_consistency(
            confirmed=confirmed, dry_run=dry_run
        )

    async def audit_records(
        self, session_id: SessionId | None = None
    ) -> tuple[AuditRecordView, ...]:
        return await self._operations.audit_records(session_id)

    async def audit_integrity(
        self,
        session_id: SessionId | None = None,
    ) -> AuditIntegrityReportView:
        return await self._operations.audit_integrity(session_id)

    # ---- Shared world projection helpers ----

    def _world_projection_views(
        self,
        session_id: SessionId,
        evidence: tuple[EvidenceView, ...],
    ) -> tuple[
        tuple[WorldFactView, ...],
        tuple[WorldFactHistoryView, ...],
        tuple[WorldEntityView, ...],
        tuple[WorldRelationView, ...],
    ]:
        from universal_agent.service.projections import world_projection_views_from_snapshot

        return world_projection_views_from_snapshot(self._world_snapshot(session_id, evidence))

    def _world_neighborhood(
        self,
        snapshot: WorldSnapshot,
        entity_id: str,
        relation: str | None,
    ) -> WorldNeighborhoodView | None:
        from universal_agent.service.projections import world_neighborhood_view

        return world_neighborhood_view(snapshot.neighborhood_for(entity_id, relation=relation))

    def _world_snapshot(
        self,
        session_id: SessionId,
        evidence: tuple[EvidenceView, ...],
    ) -> WorldSnapshot:
        return build_world_snapshot(self._components, session_id, evidence)
