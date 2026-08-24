from __future__ import annotations

from universal_agent.context import DomainContextProvider
from universal_agent.core import (
    AgentState,
    CapabilityCategory,
    CapabilityDefinition,
    DomainManifest,
    DomainMetadata,
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    Goal,
    ObservationStatus,
    PendingAction,
    SuccessCriterion,
    Task,
    ToolCall,
    ToolResult,
    immutable_json,
    new_action_id,
    new_session_id,
)
from universal_agent.domain import DomainComposition, DomainLoader, RuntimeBuilder
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import Evidence, EvidenceContext, EvidenceExtractor
from universal_agent.memory import MemoryRecord
from universal_agent.observation import ObservationFactory
from universal_agent.policy import Policy
from universal_agent.recovery import RecoveryRule
from universal_agent.runtime.processing import ObservationProcessor
from universal_agent.runtime.session import start_session
from universal_agent.tasks import TaskExpander, TaskExpansionContext, TaskSpec
from universal_agent.tools import Tool
from universal_agent.world import WorldModel, WorldUpdater


class RoutedExtractor:
    def __init__(self, owner: str, calls: list[str]) -> None:
        self.name = f"{owner}-extractor"
        self._owner = owner
        self._calls = calls

    def extract(self, context: EvidenceContext) -> tuple[Evidence, ...]:
        self._calls.append(self._owner)
        return (
            Evidence(
                context.session_id,
                context.task.id,
                context.observation.action_id,
                context.observation.id,
                f"{self._owner}/resource",
                f"{self._owner}_ready",
                True,
                self.name,
                observed_at=context.observation.observed_at,
            ),
        )


class RoutedWorldUpdater:
    def __init__(self, owner: str, calls: list[str]) -> None:
        self.name = f"{owner}-world"
        self._owner = owner
        self._calls = calls

    def apply(self, model: WorldModel, evidence: Evidence) -> bool:
        self._calls.append(f"{self._owner}:{evidence.claim}")
        return model.apply_fact(evidence)


class RoutedExpander:
    def __init__(self, owner: str, capability: str, calls: list[str]) -> None:
        self.name = f"{owner}-expander"
        self.capability_names = (capability,)
        self._owner = owner
        self._calls = calls

    def expand(self, context: TaskExpansionContext) -> tuple[TaskSpec, ...]:
        self._calls.append(self._owner)
        if context.world.value_for(f"{self._owner}_ready") is not True:
            return ()
        return (
            TaskSpec(
                f"{self._owner}-follow-up",
                f"{self._owner} routed follow-up",
                (),
                (context.task.id,),
            ),
        )


class RoutedEvaluator:
    def __init__(self, owner: str) -> None:
        self.name = f"{owner}-evaluator"
        self._owner = owner

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        key = f"{self._owner}_ready"
        complete = context.satisfied_criteria.get(key) is True
        return EvaluationResult(
            EvaluationStatus.COMPLETED if complete else EvaluationStatus.INCOMPLETE,
            f"{self._owner} routed evidence verified" if complete else f"{self._owner} missing",
            self.name,
            immutable_json({key: True} if complete else {}),
            complete,
            complete,
        )


class RoutedDomain:
    def __init__(
        self,
        owner: str,
        *,
        extractor_calls: list[str],
        updater_calls: list[str],
        expander_calls: list[str],
    ) -> None:
        self._owner = owner
        self._capability = f"inspect_{owner}"
        self._extractor = RoutedExtractor(owner, extractor_calls)
        self._updater = RoutedWorldUpdater(owner, updater_calls)
        self._expander = RoutedExpander(owner, self._capability, expander_calls)
        self._evaluator = RoutedEvaluator(owner)
        self.manifest = DomainManifest(
            "agent.nantian.dev/v1alpha1",
            "Domain",
            DomainMetadata(owner, "1.0.0", owner),
            ("Thing",),
            (self._capability,),
            (self._evaluator.name,),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition(
                self._capability,
                f"Inspect {self._owner}",
                CapabilityCategory.OBSERVATION,
            ),
        )

    def tools(self) -> tuple[Tool, ...]:
        return ()

    def policies(self) -> tuple[Policy, ...]:
        return ()

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (self._evaluator,)

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return ()

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
        return (self._extractor,)

    def world_updaters(self) -> tuple[WorldUpdater, ...]:
        return (self._updater,)

    def task_expanders(self) -> tuple[TaskExpander, ...]:
        return (self._expander,)

    def recovery_rules(self) -> tuple[RecoveryRule, ...]:
        return ()

    def memories(self) -> tuple[MemoryRecord, ...]:
        return ()


def main() -> None:
    extractor_calls: list[str] = []
    updater_calls: list[str] = []
    expander_calls: list[str] = []
    loader = DomainLoader()
    alpha = loader.load(
        RoutedDomain(
            "alpha",
            extractor_calls=extractor_calls,
            updater_calls=updater_calls,
            expander_calls=expander_calls,
        )
    )
    beta = loader.load(
        RoutedDomain(
            "beta",
            extractor_calls=extractor_calls,
            updater_calls=updater_calls,
            expander_calls=expander_calls,
        )
    )
    components = RuntimeBuilder().build(DomainComposition((alpha, beta)))
    task = Task("Inspect beta", ("beta_ready",))
    state = AgentState(
        session_id=new_session_id(),
        goal=Goal("Verify beta", (SuccessCriterion("beta_ready", True),)),
        current_task=task,
    )
    session = start_session(state, components)
    action_id = new_action_id()
    observation = ObservationFactory().from_tool_result(
        task_id=task.id,
        call=ToolCall(
            action_id,
            "beta_tool",
            "inspect_beta",
            immutable_json(),
            domain_name="beta",
            domain_version="1.0.0",
        ),
        result=ToolResult(ObservationStatus.SUCCEEDED, immutable_json({"raw": True})),
    )
    action = PendingAction(
        action_id,
        "inspect_beta",
        "beta_tool",
        "beta/resource",
        immutable_json(),
        "beta",
        "1.0.0",
    )

    processed = ObservationProcessor(components).process(session, observation, action=action)

    print(f"extractors={extractor_calls}")
    print(f"updaters={updater_calls}")
    print(f"expanders={expander_calls}")
    print(
        f"evidence_domain={processed.evidence[0].domain_name}@{processed.evidence[0].domain_version}"
    )
    print(f"evaluator={processed.evaluation.evaluator_name if processed.evaluation else None}")
    print(f"created_tasks={[task.description for task in processed.created_tasks]}")


if __name__ == "__main__":
    main()
