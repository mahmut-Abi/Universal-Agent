from __future__ import annotations

from dataclasses import dataclass, replace

from universal_agent.core import (
    DomainIdentity,
    EvaluationContext,
    EvaluationResult,
    Observation,
    PendingAction,
    Task,
    immutable_json,
)
from universal_agent.domain import RuntimeComponents
from universal_agent.evidence import Evidence, EvidenceContext
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


class ObservationRoutingError(ValueError):
    pass


class EvaluationRoutingError(ObservationRoutingError):
    pass


class ObservationProcessor:
    def __init__(self, components: RuntimeComponents) -> None:
        self._components = components

    def process(
        self,
        session: SessionRuntimeState,
        observation: Observation,
        *,
        action: PendingAction | None = None,
    ) -> ProcessingResult:
        state = session.state
        identity = self._action_domain(action)
        if (
            identity is not None
            and self._components.domain_composition.domain_for(identity) is None
        ):
            raise ObservationRoutingError(
                f"no domain registered for action domain: {identity.name}@{identity.version}"
            )
        extractors = self._components.evidence_extractors_for_domain(identity)
        extracted = tuple(
            self._stamp_evidence_owner(evidence, identity)
            for extractor in extractors
            for evidence in extractor.extract(
                EvidenceContext(state.session_id, state.current_task, observation)
            )
        )
        for evidence in extracted:
            if not session.record(evidence):
                continue
            session.apply(evidence, self._components.world_updaters_for_evidence(evidence))

        # Task-scoped, so this is the evidence the current task has accumulated,
        # not just what this one observation produced.
        accumulated = session.query()
        world = session.world()
        created = session.tasks.expand(
            tuple(
                spec
                for expander in self._components.task_expanders_for_domain(identity)
                for spec in expander.expand(
                    TaskExpansionContext(state.current_task, accumulated, world)
                )
            )
        )
        evaluator_name = self._select_evaluator_name(identity)
        evaluator = self._components.evaluators.resolve(evaluator_name)
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

    def _action_domain(self, action: PendingAction | None) -> DomainIdentity | None:
        if action is None or not action.domain_name or not action.domain_version:
            return None
        return DomainIdentity(action.domain_name, action.domain_version)

    def _select_evaluator_name(self, identity: DomainIdentity | None) -> str:
        if identity is None:
            return self._components.evaluator_names[0]
        names = self._components.domain_composition.evaluator_names_for(identity)
        if not names:
            raise EvaluationRoutingError(
                f"no evaluator registered for action domain: {identity.name}@{identity.version}"
            )
        return names[0]

    def _stamp_evidence_owner(
        self,
        evidence: Evidence,
        identity: DomainIdentity | None,
    ) -> Evidence:
        if identity is None:
            return evidence
        if evidence.domain_name == identity.name and evidence.domain_version == identity.version:
            return evidence
        if evidence.domain_name or evidence.domain_version:
            raise ObservationRoutingError(
                "evidence owner does not match action domain: "
                f"{evidence.domain_name}@{evidence.domain_version} != "
                f"{identity.name}@{identity.version}"
            )
        return replace(
            evidence,
            domain_name=identity.name,
            domain_version=identity.version,
        )
