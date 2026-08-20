from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from universal_agent.core import (
    AgentState,
    CapabilityDefinition,
    CapabilitySummary,
    ContextFragment,
    DecisionContext,
    immutable_json,
)
from universal_agent.evidence import Evidence
from universal_agent.tasks import TaskManager
from universal_agent.world import WorldSnapshot


class DomainContextProvider(Protocol):
    @property
    def name(self) -> str: ...

    def provide(self, state: AgentState) -> tuple[ContextFragment, ...]: ...


class ContextCompiler(Protocol):
    def compile(
        self,
        state: AgentState,
        capabilities: tuple[CapabilityDefinition, ...],
        policy_summary: tuple[str, ...],
        providers: tuple[DomainContextProvider, ...],
        world: WorldSnapshot | None = None,
        evidence: tuple[Evidence, ...] = (),
        tasks: TaskManager | None = None,
    ) -> DecisionContext: ...


class BasicContextCompiler:
    def __init__(self, *, max_fragments: int = 8, max_characters: int = 4_000) -> None:
        self._max_fragments = max_fragments
        self._max_characters = max_characters

    def compile(
        self,
        state: AgentState,
        capabilities: tuple[CapabilityDefinition, ...],
        policy_summary: tuple[str, ...],
        providers: tuple[DomainContextProvider, ...],
        world: WorldSnapshot | None = None,
        evidence: tuple[Evidence, ...] = (),
        tasks: TaskManager | None = None,
    ) -> DecisionContext:
        fragments = self._select_fragments(state, providers)
        return DecisionContext(
            session_id=state.session_id,
            goal_id=state.goal.id,
            goal_description=state.goal.description,
            task_id=state.current_task.id,
            task_description=state.current_task.description,
            iteration=state.iteration,
            satisfied_criteria=immutable_json(state.satisfied_criteria),
            latest_observation=state.latest_observation,
            capabilities=tuple(
                CapabilitySummary(item.name, item.description, item.category, item.risk)
                for item in capabilities
            ),
            domain_context=fragments,
            world_context=self._world_fragments(world),
            evidence_context=self._evidence_fragments(evidence),
            task_context=self._task_fragments(tasks),
            policy_summary=policy_summary,
        )

    def _select_fragments(
        self,
        state: AgentState,
        providers: tuple[DomainContextProvider, ...],
    ) -> tuple[ContextFragment, ...]:
        unique: dict[str, ContextFragment] = {}
        for provider in providers:
            for fragment in provider.provide(state):
                current = unique.get(fragment.key)
                if current is None or fragment.priority < current.priority:
                    unique[fragment.key] = fragment
        selected: list[ContextFragment] = []
        used = 0
        for fragment in sorted(unique.values(), key=lambda item: (item.priority, item.key)):
            if len(selected) >= self._max_fragments:
                break
            remaining = self._max_characters - used
            if remaining <= 0:
                break
            content = fragment.content[:remaining]
            selected.append(ContextFragment(fragment.key, content, fragment.priority))
            used += len(content)
        return tuple(selected)

    def _world_fragments(self, world: WorldSnapshot | None) -> tuple[ContextFragment, ...]:
        if world is None:
            return ()
        fragments = (
            ContextFragment(
                f"world.{fact.subject}.{fact.claim}",
                f"{fact.subject} {fact.claim}={fact.value!r} confidence={fact.confidence:.2f}",
                20,
            )
            for fact in world.facts
        )
        return self._budget_fragments(fragments)

    def _evidence_fragments(
        self,
        evidence: tuple[Evidence, ...],
    ) -> tuple[ContextFragment, ...]:
        fragments = (
            ContextFragment(
                f"evidence.{item.id}",
                f"{item.subject} {item.claim}={item.value!r} source={item.source}",
                30,
            )
            for item in sorted(
                evidence,
                key=lambda item: (item.confidence, item.observed_at, str(item.id)),
                reverse=True,
            )
        )
        return self._budget_fragments(fragments)

    def _task_fragments(self, tasks: TaskManager | None) -> tuple[ContextFragment, ...]:
        if tasks is None:
            return ()
        return self._budget_fragments(
            ContextFragment(
                f"task.{task.id}",
                f"{task.description} status={task.status.value}",
                10,
            )
            for task in tasks.all()
        )

    def _budget_fragments(
        self,
        fragments: Iterable[ContextFragment],
    ) -> tuple[ContextFragment, ...]:
        selected: list[ContextFragment] = []
        used = 0
        for fragment in fragments:
            if len(selected) >= self._max_fragments:
                break
            remaining = self._max_characters - used
            if remaining <= 0:
                break
            content = fragment.content[:remaining]
            selected.append(ContextFragment(fragment.key, content, fragment.priority))
            used += len(content)
        return tuple(selected)
