from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from universal_agent.capability import CapabilityRegistry, CapabilityResolver
from universal_agent.context import DomainContextProvider
from universal_agent.coordination import ResourceLockRegistry, ResourceVersionRegistry
from universal_agent.core import DomainIdentity
from universal_agent.domain.runtime import (
    ActionArgumentProvider,
    ActiveDomain,
    DomainComposition,
)
from universal_agent.evaluation import CriteriaEvaluator, EvaluatorRegistry
from universal_agent.evidence import (
    Evidence,
    EvidenceExtractor,
    EvidenceStore,
    InMemoryEvidenceStore,
    StructuredEvidenceExtractor,
)
from universal_agent.memory import (
    InMemoryMemoryStore,
    KeywordRelevanceFilter,
    MemoryRecord,
    MemoryStore,
    RelevanceFilter,
    StoreMemoryRetriever,
)
from universal_agent.policy import PolicyEngine
from universal_agent.recovery import RecoveryManager
from universal_agent.tasks import TaskExpander
from universal_agent.tools import ToolRegistry
from universal_agent.world import (
    FactWorldUpdater,
    InMemoryWorldModel,
    WorldModel,
    WorldUpdater,
)


def _memory_key(record: MemoryRecord) -> tuple[object, ...]:
    return (
        record.kind,
        record.subject,
        record.content,
        record.scope,
        record.confidence,
        record.source_session_id,
    )


@dataclass(frozen=True, slots=True)
class RuntimeComponents:
    """Domain-derived collaborators shared by every session of one runtime.

    Stores are typed as protocols so a persistent backend can replace the
    in-memory default without touching the runtime.
    """

    capabilities: CapabilityRegistry
    tools: ToolRegistry
    resolver: CapabilityResolver
    policy_engine: PolicyEngine
    evaluators: EvaluatorRegistry
    evidence_store: EvidenceStore
    world_model: WorldModel
    world_updaters: tuple[WorldUpdater, ...]
    default_evidence_extractors: tuple[EvidenceExtractor, ...]
    default_world_updaters: tuple[WorldUpdater, ...]
    recovery_manager: RecoveryManager
    active_domain: ActiveDomain
    domain_composition: DomainComposition
    context_providers: tuple[DomainContextProvider, ...]
    evidence_extractors: tuple[EvidenceExtractor, ...]
    task_expanders: tuple[TaskExpander, ...]
    action_argument_providers: tuple[ActionArgumentProvider, ...]
    evaluator_names: tuple[str, ...]
    memory_scope: str | None
    memory_store: MemoryStore
    memory_retriever: StoreMemoryRetriever
    memory_filter: RelevanceFilter
    resource_locks: ResourceLockRegistry
    resource_versions: ResourceVersionRegistry

    def evidence_extractors_for_domain(
        self,
        identity: DomainIdentity | None,
    ) -> tuple[EvidenceExtractor, ...]:
        if identity is None:
            return self.evidence_extractors or self.default_evidence_extractors
        extractors = self.domain_composition.evidence_extractors_for(identity)
        return extractors or self.default_evidence_extractors

    def world_updaters_for_domain(
        self,
        identity: DomainIdentity | None,
    ) -> tuple[WorldUpdater, ...]:
        if identity is None:
            return self.world_updaters
        updaters = self.domain_composition.world_updaters_for(identity)
        return updaters or self.default_world_updaters

    def world_updaters_for_evidence(self, evidence: Evidence) -> tuple[WorldUpdater, ...]:
        if evidence.domain_name and evidence.domain_version:
            return self.world_updaters_for_domain(
                DomainIdentity(evidence.domain_name, evidence.domain_version)
            )
        return self.world_updaters

    def task_expanders_for_domain(
        self,
        identity: DomainIdentity | None,
    ) -> tuple[TaskExpander, ...]:
        if identity is None:
            return self.task_expanders
        return self.domain_composition.task_expanders_for(identity)

    def action_argument_providers_for_domain(
        self,
        identity: DomainIdentity | None,
    ) -> tuple[ActionArgumentProvider, ...]:
        if identity is None:
            return self.action_argument_providers
        return self.domain_composition.action_argument_providers_for(identity)


class RuntimeBuilder:
    """Assemble RuntimeComponents from an activated domain.

    The store factories exist so two runtimes can share one evidence store and
    world model; by default each build gets its own in-memory pair, which is
    what makes cross-runtime tests exercise the snapshot instead of shared
    object identity.
    """

    def __init__(
        self,
        *,
        evidence_store_factory: Callable[[], EvidenceStore] = InMemoryEvidenceStore,
        world_model_factory: Callable[[], WorldModel] = InMemoryWorldModel,
        memory_store_factory: Callable[[], MemoryStore] = InMemoryMemoryStore,
        resource_lock_factory: Callable[[], ResourceLockRegistry] = ResourceLockRegistry,
        resource_version_factory: Callable[
            [],
            ResourceVersionRegistry,
        ] = ResourceVersionRegistry,
        memory_filter: RelevanceFilter | None = None,
    ) -> None:
        self._evidence_store_factory = evidence_store_factory
        self._world_model_factory = world_model_factory
        self._memory_store_factory = memory_store_factory
        self._resource_lock_factory = resource_lock_factory
        self._resource_version_factory = resource_version_factory
        self._memory_filter = memory_filter

    def build(self, domain: ActiveDomain | DomainComposition) -> RuntimeComponents:
        composition = (
            domain if isinstance(domain, DomainComposition) else DomainComposition.single(domain)
        )
        capabilities = CapabilityRegistry()
        for active_domain in composition.domains:
            for capability in active_domain.capabilities:
                capabilities.register(capability, active_domain.identity)
        tools = ToolRegistry()
        for active_domain in composition.domains:
            for tool in active_domain.tools:
                tools.register(tool, active_domain.identity)
        evaluators = EvaluatorRegistry()
        evaluators.register(CriteriaEvaluator())
        for evaluator in composition.evaluators():
            if evaluator.name != CriteriaEvaluator.name:
                evaluators.register(evaluator)
        memory_store = self._memory_store_factory()
        existing = {_memory_key(record) for record in memory_store.export()}
        for record in composition.memories():
            key = _memory_key(record)
            if key in existing:
                continue
            memory_store.add(record)
            existing.add(key)
        memory_filter = self._memory_filter or KeywordRelevanceFilter()
        return RuntimeComponents(
            capabilities=capabilities,
            tools=tools,
            resolver=CapabilityResolver(capabilities, tools),
            policy_engine=PolicyEngine(composition.policies()),
            evaluators=evaluators,
            evidence_store=self._evidence_store_factory(),
            world_model=self._world_model_factory(),
            world_updaters=composition.world_updaters() or (FactWorldUpdater(),),
            default_evidence_extractors=(StructuredEvidenceExtractor(),),
            default_world_updaters=(FactWorldUpdater(),),
            recovery_manager=RecoveryManager(composition.recovery_rules()),
            active_domain=composition.primary,
            domain_composition=composition,
            context_providers=composition.context_providers(),
            evidence_extractors=composition.evidence_extractors(),
            task_expanders=composition.task_expanders(),
            action_argument_providers=composition.action_argument_providers(),
            evaluator_names=composition.evaluator_names(),
            memory_scope=composition.scope,
            memory_store=memory_store,
            memory_retriever=StoreMemoryRetriever(memory_store),
            memory_filter=memory_filter,
            resource_locks=self._resource_lock_factory(),
            resource_versions=self._resource_version_factory(),
        )
