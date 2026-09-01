from __future__ import annotations

from dataclasses import dataclass, field

from universal_agent.core import SessionId
from universal_agent.distributed import DistributedHealthReport, DistributedRuntimeSnapshot
from universal_agent.operations import (
    AuditRecordView,
    DoctorReportView,
    RuntimeCostView,
    RuntimeMetricsView,
)
from universal_agent.runtime import RuntimeEventView, SessionSummaryView, SessionView
from universal_agent.service import (
    CapabilityView,
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
    RuntimeService,
    SessionExplorerView,
    ToolView,
    WorldNeighborhoodView,
)


@dataclass(frozen=True, slots=True)
class RuntimeConsoleSnapshot:
    health: HealthView
    ready: ReadyView
    config: RuntimeConfigView
    domains: tuple[DomainView, ...]
    domain_packages: tuple[DomainPackageView, ...]
    profiles: tuple[ProfileView, ...]
    capabilities: tuple[CapabilityView, ...]
    tools: tuple[ToolView, ...]
    policies: tuple[PolicyView, ...]
    evaluators: tuple[EvaluatorView, ...]
    memories: tuple[MemoryView, ...]
    metrics: RuntimeMetricsView
    cost: RuntimeCostView
    doctor: DoctorReportView
    distributed_snapshot: DistributedRuntimeSnapshot | None
    distributed_health: DistributedHealthReport | None
    sessions: tuple[SessionSummaryView, ...]
    selected_session: SessionView | None
    session_explorer: SessionExplorerView | None
    events: tuple[RuntimeEventView, ...]
    audit_records: tuple[AuditRecordView, ...]
    world_neighborhood: WorldNeighborhoodView | None = None
    multi_agent: MultiAgentView = field(default_factory=lambda: MultiAgentView(False))


async def build_runtime_console_snapshot(
    service: RuntimeService,
    *,
    session_id: SessionId | None = None,
    session_limit: int = 5,
    event_limit: int = 12,
    world_entity_id: str | None = None,
    world_relation: str | None = None,
) -> RuntimeConsoleSnapshot:
    """Build a read-only application snapshot from RuntimeService projections."""

    if world_relation is not None and world_entity_id is None:
        raise ValueError("world relation filter requires entity_id")

    session_batch = await service.stream_sessions(limit=session_limit)
    selected_session_id = session_id
    if selected_session_id is None and session_batch.sessions:
        selected_session_id = session_batch.sessions[0].session_id

    selected_session: SessionView | None = None
    session_explorer: SessionExplorerView | None = None
    world_neighborhood: WorldNeighborhoodView | None = None
    events: tuple[RuntimeEventView, ...] = ()
    audit_records: tuple[AuditRecordView, ...] = ()
    if selected_session_id is not None:
        session_explorer = await service.session_explorer(selected_session_id)
        selected_session = session_explorer.session
        if world_entity_id is not None:
            world_neighborhood = (
                await service.session_world(
                    selected_session_id,
                    entity_id=world_entity_id,
                    relation=world_relation,
                )
            ).neighborhood
        events = (
            await service.stream_events(
                selected_session_id,
                limit=event_limit,
            )
        ).events
        audit_records = await service.audit_records(selected_session_id)

    return RuntimeConsoleSnapshot(
        health=service.health(),
        ready=service.ready(),
        config=service.config(),
        domains=service.domains(),
        domain_packages=service.domain_packages(),
        profiles=service.profiles(),
        capabilities=service.capabilities(),
        tools=service.tools(),
        policies=service.policies(),
        evaluators=service.evaluators(),
        memories=service.memories(),
        metrics=await service.metrics(),
        cost=await service.cost(),
        doctor=await service.doctor(),
        distributed_snapshot=service.distributed_snapshot(),
        distributed_health=service.distributed_health(),
        sessions=session_batch.sessions,
        selected_session=selected_session,
        session_explorer=session_explorer,
        events=events,
        audit_records=audit_records,
        world_neighborhood=world_neighborhood,
        multi_agent=service.multi_agent(),
    )


__all__ = ["RuntimeConsoleSnapshot", "build_runtime_console_snapshot"]
