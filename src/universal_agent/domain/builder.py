from __future__ import annotations

from dataclasses import dataclass

from universal_agent.capability import CapabilityRegistry, CapabilityResolver
from universal_agent.domain.runtime import ActiveDomain
from universal_agent.evaluation import CriteriaEvaluator, EvaluatorRegistry
from universal_agent.evidence import InMemoryEvidenceStore
from universal_agent.policy import PolicyEngine
from universal_agent.recovery import RecoveryManager
from universal_agent.tools import ToolRegistry
from universal_agent.world import FactWorldUpdater, InMemoryWorldModel, WorldUpdater


@dataclass(frozen=True, slots=True)
class RuntimeComponents:
    capabilities: CapabilityRegistry
    tools: ToolRegistry
    resolver: CapabilityResolver
    policy_engine: PolicyEngine
    evaluators: EvaluatorRegistry
    evidence_store: InMemoryEvidenceStore
    world_model: InMemoryWorldModel
    world_updaters: tuple[WorldUpdater, ...]
    recovery_manager: RecoveryManager
    active_domain: ActiveDomain


class RuntimeBuilder:
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
        return RuntimeComponents(
            capabilities=capabilities,
            tools=tools,
            resolver=CapabilityResolver(capabilities, tools),
            policy_engine=PolicyEngine(domain.policies),
            evaluators=evaluators,
            evidence_store=InMemoryEvidenceStore(),
            world_model=InMemoryWorldModel(),
            world_updaters=domain.world_updaters or (FactWorldUpdater(),),
            recovery_manager=RecoveryManager(domain.recovery_rules),
            active_domain=domain,
        )
