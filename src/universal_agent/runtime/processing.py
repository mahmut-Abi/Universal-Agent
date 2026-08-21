from __future__ import annotations

from dataclasses import dataclass

from universal_agent.core import (
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
    StructuredEvidenceExtractor,
)
from universal_agent.runtime.session import (
    SessionRuntimeState,
    complete_current_task,
    start_next_task,
)
from universal_agent.tasks import TaskExpansionContext


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
        session: SessionRuntimeState,
        observation: Observation,
    ) -> ProcessingResult:
        state = session.state
        extractors = self._components.evidence_extractors or (
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
            if not session.record(evidence):
                continue
            session.apply(evidence, self._components)

        # Task-scoped, so this is the evidence the current task has accumulated,
        # not just what this one observation produced.
        accumulated = session.query()
        world = session.world()
        created = session.tasks.expand(
            tuple(
                spec
                for expander in self._components.task_expanders
                for spec in expander.expand(
                    TaskExpansionContext(state.current_task, accumulated, world)
                )
            )
        )
        evaluator = self._components.evaluators.resolve(self._components.evaluator_names[0])
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
                accumulated,
                world,
            )
        )
        state.latest_evaluation = evaluation
        state.satisfied_criteria.update(evaluation.matched_criteria)
        next_task = None
        if evaluation.task_completed:
            previous_id = session.tasks.current.id
            complete_current_task(session)
            start_next_task(session)
            current = session.tasks.current
            next_task = current if current.id != previous_id else None
        session.sync_current_task()
        return ProcessingResult(extracted, evaluation, created, next_task)
