from __future__ import annotations

from dataclasses import dataclass

from universal_agent.core import SessionId
from universal_agent.operations import AuditRecordView, RuntimeCostView, RuntimeMetricsView
from universal_agent.runtime import RuntimeEventView, SessionSummaryView, SessionView
from universal_agent.service import (
    CapabilityView,
    DomainView,
    EvaluatorView,
    HealthView,
    PolicyView,
    ProfileView,
    ReadyView,
    RuntimeConfigView,
    RuntimeService,
    SessionExplorerView,
    ToolView,
)


@dataclass(frozen=True, slots=True)
class RuntimeConsoleSnapshot:
    health: HealthView
    ready: ReadyView
    config: RuntimeConfigView
    domains: tuple[DomainView, ...]
    profiles: tuple[ProfileView, ...]
    capabilities: tuple[CapabilityView, ...]
    tools: tuple[ToolView, ...]
    policies: tuple[PolicyView, ...]
    evaluators: tuple[EvaluatorView, ...]
    metrics: RuntimeMetricsView
    cost: RuntimeCostView
    sessions: tuple[SessionSummaryView, ...]
    selected_session: SessionView | None
    session_explorer: SessionExplorerView | None
    events: tuple[RuntimeEventView, ...]
    audit_records: tuple[AuditRecordView, ...]


async def build_runtime_console_snapshot(
    service: RuntimeService,
    *,
    session_id: SessionId | None = None,
    session_limit: int = 5,
    event_limit: int = 12,
) -> RuntimeConsoleSnapshot:
    """Build a read-only application snapshot from RuntimeService projections."""

    session_batch = await service.stream_sessions(limit=session_limit)
    selected_session_id = session_id
    if selected_session_id is None and session_batch.sessions:
        selected_session_id = session_batch.sessions[0].session_id

    selected_session: SessionView | None = None
    session_explorer: SessionExplorerView | None = None
    events: tuple[RuntimeEventView, ...] = ()
    audit_records: tuple[AuditRecordView, ...] = ()
    if selected_session_id is not None:
        session_explorer = await service.session_explorer(selected_session_id)
        selected_session = session_explorer.session
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
        profiles=service.profiles(),
        capabilities=service.capabilities(),
        tools=service.tools(),
        policies=service.policies(),
        evaluators=service.evaluators(),
        metrics=await service.metrics(),
        cost=await service.cost(),
        sessions=session_batch.sessions,
        selected_session=selected_session,
        session_explorer=session_explorer,
        events=events,
        audit_records=audit_records,
    )


__all__ = ["RuntimeConsoleSnapshot", "build_runtime_console_snapshot"]
