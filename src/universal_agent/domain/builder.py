from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from universal_agent.capability import CapabilityRegistry, CapabilityResolver
from universal_agent.domain.runtime import ActiveDomain
from universal_agent.evaluation import CriteriaEvaluator, EvaluatorRegistry
from universal_agent.evidence import EvidenceStore, InMemoryEvidenceStore
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
    recovery_manager: RecoveryManager
    active_domain: ActiveDomain
    memory_store: MemoryStore
    memory_retriever: StoreMemoryRetriever
    memory_filter: RelevanceFilter


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
        memory_filter: RelevanceFilter | None = None,
    ) -> None:
        self._evidence_store_factory = evidence_store_factory
        self._world_model_factory = world_model_factory
        self._memory_store_factory = memory_store_factory
        self._memory_filter = memory_filter

    def build(self, domain: ActiveDomain) -> RuntimeComponents:
        capabilities = CapabilityRegistry()
        for capability in domain.capabilities:
            capabilities.register(capability)
        tools = ToolRegistry()
        for tool in domain.tools:
            tools.register(tool)
        evaluators = EvaluatorRegistry()
        evaluators.register(CriteriaEvaluator())
        for evaluator in domain.evaluators:
            if evaluator.name != CriteriaEvaluator.name:
                evaluators.register(evaluator)
        memory_store = self._memory_store_factory()
        existing = {_memory_key(record) for record in memory_store.export()}
        for record in domain.memories:
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
            policy_engine=PolicyEngine(domain.policies),
            evaluators=evaluators,
            evidence_store=self._evidence_store_factory(),
            world_model=self._world_model_factory(),
            world_updaters=domain.world_updaters or (FactWorldUpdater(),),
            recovery_manager=RecoveryManager(domain.recovery_rules),
            active_domain=domain,
            memory_store=memory_store,
            memory_retriever=StoreMemoryRetriever(memory_store),
            memory_filter=memory_filter,
        )
