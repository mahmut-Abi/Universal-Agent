from __future__ import annotations

from dataclasses import dataclass

from universal_agent.core import (
    AgentState,
    EvaluationContext,
    EvaluationResult,
    Observation,
    Task,
    immutable_json,
)
from universal_agent.domain import RuntimeComponents
from universal_agent.evidence import (
    Evidence,
    EvidenceContext,
    EvidenceQuery,
    StructuredEvidenceExtractor,
)
from universal_agent.tasks import TaskExpansionContext, TaskManager


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    evidence: tuple[Evidence, ...]
    evaluation: EvaluationResult | None
    created_tasks: tuple[Task, ...]
    next_task: Task | None


class ObservationProcessor:
    def __init__(self, components: RuntimeComponents) -> None:
        self._components = components

    def process(
        self,
        state: AgentState,
        observation: Observation,
        tasks: TaskManager,
    ) -> ProcessingResult:
        extractors = self._components.active_domain.evidence_extractors or (
            StructuredEvidenceExtractor(),
        )
        extracted = tuple(
            evidence
            for extractor in extractors
            for evidence in extractor.extract(
                EvidenceContext(state.session_id, state.current_task, observation)
            )
        )
        for evidence in extracted:
            if not self._components.evidence_store.add(evidence):
                continue
            for updater in self._components.world_updaters:
                updater.apply(self._components.world_model, evidence)

        evidence = self._components.evidence_store.query(
            EvidenceQuery(state.session_id, task_id=state.current_task.id)
        )
        world = self._components.world_model.snapshot(state.session_id)
        created = tasks.expand(
            tuple(
                spec
                for expander in self._components.active_domain.task_expanders
                for spec in expander.expand(
                    TaskExpansionContext(state.current_task, evidence, world)
                )
            )
        )
        evaluator = self._components.evaluators.resolve(
            self._components.active_domain.manifest.evaluator_names[0]
        )
        criteria = {
            fact.claim: fact.value
            for fact in world.facts
            if fact.claim in {item.key for item in state.goal.success_criteria}
            or fact.claim in state.current_task.required_criteria
        }
        evaluation = evaluator.evaluate(
            EvaluationContext(
                state.goal,
                state.current_task,
                observation,
                immutable_json(criteria),
                evidence,
                world,
            )
        )
        state.latest_evaluation = evaluation
        state.satisfied_criteria.update(evaluation.matched_criteria)
        next_task = None
        if evaluation.task_completed:
            tasks.complete_current()
            next_task = tasks.start_next()
            state.current_task = next_task or tasks.current
        return ProcessingResult(extracted, evaluation, created, next_task)
