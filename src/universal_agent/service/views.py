from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from universal_agent.core import (
    CapabilityCategory,
    DomainIdentity,
    JsonMapping,
    JsonValue,
    PolicyEffect,
    RiskLevel,
    SessionId,
    SideEffect,
    immutable_json,
)
from universal_agent.distributed import (
    DistributedHealthReport,
    DistributedRuntimeSnapshot,
    WorkItem,
)
from universal_agent.memory import MemoryKind
from universal_agent.multi_agent import AgentInstanceStatus
from universal_agent.runtime import EvidenceView, RuntimeEventView, SessionView


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
    resource_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CapabilityView:
    name: str
    description: str
    category: CapabilityCategory
    risk: RiskLevel
    domain_name: str
    domain_version: str
    tool_names: tuple[str, ...]
    required_arguments: tuple[str, ...] = ()
    argument_schema: JsonMapping = field(default_factory=immutable_json)


@dataclass(frozen=True, slots=True)
class ToolView:
    name: str
    description: str
    capabilities: tuple[str, ...]
    required_arguments: tuple[str, ...]
    argument_schema: JsonMapping
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
class MultiAgentProfileView:
    name: str
    version: str
    domains: tuple[DomainIdentity, ...]
    permissions: tuple[str, ...]
    capabilities: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True, slots=True)
class MultiAgentInstanceView:
    agent_id: str
    profile_name: str
    profile_version: str
    status: AgentInstanceStatus
    session_id: SessionId | None = None
    endpoint: str | None = None


@dataclass(frozen=True, slots=True)
class MultiAgentDelegationTaskView:
    task_id: str
    child_count: int
    delegation_depth: int | None


@dataclass(frozen=True, slots=True)
class MultiAgentView:
    enabled: bool
    profiles: tuple[MultiAgentProfileView, ...] = ()
    instances: tuple[MultiAgentInstanceView, ...] = ()
    delegation_tasks: tuple[MultiAgentDelegationTaskView, ...] = ()

    @property
    def profile_count(self) -> int:
        return len(self.profiles)

    @property
    def instance_count(self) -> int:
        return len(self.instances)

    @property
    def ready_instance_count(self) -> int:
        return _multi_agent_instance_status_count(self.instances, AgentInstanceStatus.READY)

    @property
    def busy_instance_count(self) -> int:
        return _multi_agent_instance_status_count(self.instances, AgentInstanceStatus.BUSY)

    @property
    def draining_instance_count(self) -> int:
        return _multi_agent_instance_status_count(self.instances, AgentInstanceStatus.DRAINING)

    @property
    def offline_instance_count(self) -> int:
        return _multi_agent_instance_status_count(self.instances, AgentInstanceStatus.OFFLINE)

    @property
    def delegation_task_count(self) -> int:
        return len(self.delegation_tasks)


REDACTED_ENVIRONMENT_VALUE = "<redacted>"


@dataclass(frozen=True, slots=True)
class RuntimeConfigDomainView:
    name: str
    version: str
    primary: bool
    backend: str | None = None
    settings: JsonMapping = field(default_factory=immutable_json)


@dataclass(frozen=True, slots=True)
class RuntimeSecretRefView:
    name: str
    source: str
    key: str
    required: bool
    available: bool | None = None
    status: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeModelConfigView:
    provider: str = "scripted"
    name: str = "scripted"
    endpoint: str | None = None
    api_key_secret: str | None = None
    timeout_seconds: float = 30.0
    headers: JsonMapping = field(default_factory=immutable_json)
    response_format: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeConfigView:
    environment: JsonMapping
    domain_package_paths: tuple[str, ...]
    store_backend: str
    store_path: str | None
    distributed_queue_backend: str
    distributed_queue_path: str | None
    distributed_locks_backend: str
    distributed_locks_path: str | None
    distributed_workers_backend: str
    distributed_workers_path: str | None
    max_iterations: int
    max_recovery_steps: int
    domains: tuple[RuntimeConfigDomainView, ...]
    model: RuntimeModelConfigView = field(default_factory=RuntimeModelConfigView)
    secrets: tuple[RuntimeSecretRefView, ...] = ()
    distributed_terminal_retention_seconds: float | None = None
    state_event_commit_supported: bool | None = None
    state_event_commit_strategy: str | None = None
    state_event_commit_shared_store: bool | None = None


@dataclass(frozen=True, slots=True)
class StateEventRepairView:
    event: RuntimeEventView
    reason: str


@dataclass(frozen=True, slots=True)
class StateEventRepairSkipView:
    session_id: SessionId
    event_id: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class StateEventRepairReport:
    status: str
    repairs: tuple[StateEventRepairView, ...]
    skipped: tuple[StateEventRepairSkipView, ...]

    @property
    def repaired_event_count(self) -> int:
        return len(self.repairs)

    @property
    def skipped_item_count(self) -> int:
        return len(self.skipped)


@dataclass(frozen=True, slots=True)
class WorldFactView:
    subject: str
    claim: str
    value: JsonValue
    confidence: float
    observed_at: datetime
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorldFactEvidenceView:
    evidence_id: str
    value: JsonValue
    confidence: float
    observed_at: datetime
    source: str


@dataclass(frozen=True, slots=True)
class WorldFactHistoryView:
    subject: str
    claim: str
    current: WorldFactView
    candidates: tuple[WorldFactEvidenceView, ...]
    conflicting: bool


@dataclass(frozen=True, slots=True)
class WorldEntityView:
    entity_id: str
    kind: str
    attributes: JsonMapping
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorldRelationView:
    source: str
    relation: str
    target: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorldNeighborhoodView:
    root: WorldEntityView | None
    facts: tuple[WorldFactView, ...]
    outgoing_relations: tuple[WorldRelationView, ...]
    incoming_relations: tuple[WorldRelationView, ...]
    related_entities: tuple[WorldEntityView, ...]


@dataclass(frozen=True, slots=True)
class SessionWorldView:
    session_id: SessionId
    world_facts: tuple[WorldFactView, ...]
    world_fact_histories: tuple[WorldFactHistoryView, ...]
    world_entities: tuple[WorldEntityView, ...]
    world_relations: tuple[WorldRelationView, ...]
    neighborhood: WorldNeighborhoodView | None = None


@dataclass(frozen=True, slots=True)
class DistributedPendingActionSchedulingResult:
    scheduled_work_items: tuple[WorkItem, ...]
    snapshot: DistributedRuntimeSnapshot
    health: DistributedHealthReport


@dataclass(frozen=True, slots=True)
class SessionExplorerView:
    session: SessionView
    evidence: tuple[EvidenceView, ...]
    world_facts: tuple[WorldFactView, ...]
    world_entities: tuple[WorldEntityView, ...] = ()
    world_relations: tuple[WorldRelationView, ...] = ()
    world_fact_histories: tuple[WorldFactHistoryView, ...] = ()


def _multi_agent_instance_status_count(
    instances: tuple[MultiAgentInstanceView, ...],
    status: AgentInstanceStatus,
) -> int:
    return sum(1 for instance in instances if instance.status is status)
