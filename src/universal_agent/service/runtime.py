from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from universal_agent.core import (
    CapabilityCategory,
    DomainIdentity,
    EventId,
    Goal,
    JsonMapping,
    JsonValue,
    PolicyEffect,
    RiskLevel,
    SessionId,
    SideEffect,
    Task,
    immutable_json,
)
from universal_agent.distributed import (
    DistributedCancellationResult,
    DistributedHealthReport,
    DistributedMaintenanceResult,
    DistributedRuntimeCoordinator,
    DistributedRuntimeSnapshot,
    WorkItemId,
)
from universal_agent.domain import ActiveDomain, RuntimeComponents
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
    ) -> None:
        self._runtime_api = runtime_api
        self._components = components
        self._profiles = ProfileRegistry(profiles)
        self._config = config
        self._distributed_coordinator = distributed_coordinator

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
        return self._profiles.has(name)

    def config(self) -> RuntimeConfigView:
        identities = self._components.domain_composition.identities
        if self._config is None:
            return RuntimeConfigView(
                environment=immutable_json(),
                store_backend="memory",
                store_path=None,
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
        events: list[RuntimeEventView] = []
        for session in sessions:
            events.extend(await self.list_events(session.session_id))
        return tuple(sorted(events, key=lambda event: event.occurred_at))

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
