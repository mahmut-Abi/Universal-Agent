from __future__ import annotations

from typing import TYPE_CHECKING

from universal_agent.core import JsonMapping, SessionId
from universal_agent.domain import RuntimeComponents
from universal_agent.operations import (
    AuditIntegrityReportView,
    AuditRecordView,
    DoctorReportView,
    RuntimeCostView,
    RuntimeLogRecordView,
    RuntimeMetricsView,
    RuntimeTraceSpanView,
    build_audit_integrity,
    build_audit_records,
    build_doctor_report,
    build_opentelemetry_trace_export,
    build_prometheus_metrics_export,
    build_runtime_cost,
    build_runtime_logs,
    build_runtime_metrics,
    build_runtime_trace_spans,
)
from universal_agent.runtime import (
    RuntimeAPI,
    RuntimeEventView,
    SessionSummaryView,
)
from universal_agent.security import SecretResolutionReport
from universal_agent.service.config_views import secret_scan_payload
from universal_agent.service.distributed_runtime import DistributedRuntimeController
from universal_agent.service.state_event_repair import (
    missing_terminal_state_events,
    planned_state_event_repair_view,
    state_event_repair_view,
    unrepairable_state_event_items,
)
from universal_agent.service.views import StateEventRepairReport

if TYPE_CHECKING:
    from universal_agent.host.config import RuntimeConfig
    from universal_agent.service.catalog_service import CatalogService


class OperationsService:
    """Runtime operations surface: metrics, cost, logs, traces, doctor, repair.

    All reads are derived from session summaries and event projections; nothing
    here mutates runtime state except the deliberate state/event repair path.
    `doctor` reuses the catalog service for health/readiness/config so those
    projections are defined in exactly one place.
    """

    def __init__(
        self,
        *,
        runtime_api: RuntimeAPI,
        components: RuntimeComponents,
        distributed_runtime: DistributedRuntimeController,
        secret_resolution: SecretResolutionReport | None,
        config: RuntimeConfig | None,
        catalog: CatalogService,
    ) -> None:
        self._runtime_api = runtime_api
        self._components = components
        self._distributed_runtime = distributed_runtime
        self._secret_resolution = secret_resolution
        self._config = config
        self._catalog = catalog

    async def metrics(self) -> RuntimeMetricsView:
        sessions = await self._runtime_api.list_sessions()
        return build_runtime_metrics(sessions, await self._list_all_events(sessions))

    async def prometheus_metrics(self) -> str:
        return build_prometheus_metrics_export(await self.metrics())

    async def cost(self, session_id: SessionId | None = None) -> RuntimeCostView:
        if session_id is not None:
            return build_runtime_cost(await self._runtime_api.list_events(session_id))
        sessions = await self._runtime_api.list_sessions()
        return build_runtime_cost(await self._list_all_events(sessions))

    async def logs(
        self,
        session_id: SessionId | None = None,
    ) -> tuple[RuntimeLogRecordView, ...]:
        if session_id is not None:
            return build_runtime_logs(
                await self._runtime_api.list_events(session_id),
                session_id=session_id,
            )
        sessions = await self._runtime_api.list_sessions()
        return build_runtime_logs(await self._list_all_events(sessions))

    async def traces(
        self,
        session_id: SessionId | None = None,
    ) -> tuple[RuntimeTraceSpanView, ...]:
        if session_id is not None:
            return build_runtime_trace_spans(
                await self._runtime_api.list_events(session_id),
                session_id=session_id,
            )
        sessions = await self._runtime_api.list_sessions()
        return build_runtime_trace_spans(await self._list_all_events(sessions))

    async def opentelemetry_traces(
        self,
        session_id: SessionId | None = None,
    ) -> JsonMapping:
        return build_opentelemetry_trace_export(await self.traces(session_id))

    async def doctor(self) -> DoctorReportView:
        health = self._catalog.health()
        ready = self._catalog.ready()
        config = self._catalog.config()
        sessions = await self._runtime_api.list_sessions()
        events = await self._list_all_events(sessions)
        distributed_health = self._distributed_runtime.health()
        distributed_snapshot = self._distributed_runtime.snapshot()
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

        sessions = await self._runtime_api.list_sessions()
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
                await self._runtime_api.list_events(session_id),
                session_id=session_id,
            )
        sessions = await self._runtime_api.list_sessions()
        return build_audit_records(await self._list_all_events(sessions))

    async def audit_integrity(
        self,
        session_id: SessionId | None = None,
    ) -> AuditIntegrityReportView:
        return build_audit_integrity(await self.audit_records(session_id))

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
            events.extend(await self._runtime_api.list_events(session.session_id))
        return tuple(sorted(events, key=lambda event: event.occurred_at))
