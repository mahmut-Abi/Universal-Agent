from __future__ import annotations

import asyncio

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DomainLoader,
    Goal,
    InMemoryEventSink,
    InMemoryStateStore,
    RuntimeBuilder,
    ScriptedModelAdapter,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.context import DomainContextProvider
from universal_agent.core import (
    CapabilityCategory,
    CapabilityDefinition,
    DomainManifest,
    DomainMetadata,
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    JsonMapping,
    ToolDefinition,
)
from universal_agent.domain import DomainComposition
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import EvidenceExtractor
from universal_agent.memory import MemoryRecord
from universal_agent.policy import Policy
from universal_agent.recovery import RecoveryRule
from universal_agent.tasks import TaskExpander
from universal_agent.tools import Tool
from universal_agent.world import WorldUpdater


class StaticTool:
    def __init__(
        self,
        name: str,
        capability: str,
        output: JsonMapping,
    ) -> None:
        self.definition = ToolDefinition(name, name, (capability,))
        self._output = output

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return self._output


class CriterionEvaluator:
    def __init__(self, name: str, criterion: str) -> None:
        self.name = name
        self._criterion = criterion

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        complete = context.satisfied_criteria.get(self._criterion) is True
        matched = {self._criterion: True} if complete else {}
        return EvaluationResult(
            EvaluationStatus.COMPLETED if complete else EvaluationStatus.INCOMPLETE,
            f"{self._criterion} satisfied" if complete else f"{self._criterion} missing",
            self.name,
            immutable_json(matched),
            complete,
            complete,
        )


class StaticDomain:
    def __init__(
        self,
        *,
        name: str,
        capability: str,
        tool_name: str,
        evaluator: Evaluator,
        output: JsonMapping,
    ) -> None:
        self.manifest = DomainManifest(
            "agent.nantian.dev/v1alpha1",
            "Domain",
            DomainMetadata(name, "1.0.0", name),
            ("Thing",),
            (capability,),
            (evaluator.name,),
        )
        self._capability = capability
        self._tool = StaticTool(tool_name, capability, output)
        self._evaluator = evaluator

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition(
                self._capability,
                self._capability,
                CapabilityCategory.OBSERVATION,
            ),
        )

    def tools(self) -> tuple[Tool, ...]:
        return (self._tool,)

    def policies(self) -> tuple[Policy, ...]:
        return ()

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (self._evaluator,)

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return ()

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
        return ()

    def world_updaters(self) -> tuple[WorldUpdater, ...]:
        return ()

    def task_expanders(self) -> tuple[TaskExpander, ...]:
        return ()

    def recovery_rules(self) -> tuple[RecoveryRule, ...]:
        return ()

    def memories(self) -> tuple[MemoryRecord, ...]:
        return ()


async def main() -> None:
    loader = DomainLoader()
    alpha = loader.load(
        StaticDomain(
            name="alpha",
            capability="inspect_alpha",
            tool_name="alpha_inspect",
            evaluator=CriterionEvaluator("alpha-evaluator", "alpha_ready"),
            output=immutable_json({"alpha_ready": True}),
        )
    )
    beta = loader.load(
        StaticDomain(
            name="beta",
            capability="inspect_beta",
            tool_name="beta_inspect",
            evaluator=CriterionEvaluator("beta-evaluator", "beta_ready"),
            output=immutable_json({"beta_ready": True}),
        )
    )
    components = RuntimeBuilder().build(DomainComposition((alpha, beta)))
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                Decision(
                    DecisionType.EXECUTE,
                    "Inspect beta readiness",
                    capability="inspect_beta",
                    expected_observations=("beta_ready",),
                ),
                Decision(DecisionType.FINISH, "Beta domain evidence is verified"),
            ]
        ),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=events,
    )

    result = await runtime.run(
        Goal("Verify beta domain", (SuccessCriterion("beta_ready", True),)),
        Task("Inspect beta", ("beta_ready",)),
    )
    evaluation = next(event for event in events.events if event.type == "EvaluationCompleted")
    print(f"status={result.status.value}")
    print(f"evaluator={evaluation.data['evaluator']}")
    print(
        "domains="
        + ", ".join(identity.name for identity in components.domain_composition.identities)
    )


if __name__ == "__main__":
    asyncio.run(main())
