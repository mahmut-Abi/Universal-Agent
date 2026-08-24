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
from universal_agent.memory import MemoryRecord
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
        memories: tuple[MemoryRecord, ...] = (),
    ) -> DecisionContext: ...


class BasicContextCompiler:
    def __init__(
        self,
        *,
        max_fragments: int = 8,
        max_characters: int = 4_000,
        max_memory_fragments: int = 4,
        max_memory_characters: int = 1_200,
    ) -> None:
        self._max_fragments = max_fragments
        self._max_characters = max_characters
        self._max_memory_fragments = max_memory_fragments
        self._max_memory_characters = max_memory_characters

    def compile(
        self,
        state: AgentState,
        capabilities: tuple[CapabilityDefinition, ...],
        policy_summary: tuple[str, ...],
        providers: tuple[DomainContextProvider, ...],
        world: WorldSnapshot | None = None,
        evidence: tuple[Evidence, ...] = (),
        tasks: TaskManager | None = None,
        memories: tuple[MemoryRecord, ...] = (),
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
            memory_context=self._memory_fragments(memories),
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
        fragments = [
            *(
                ContextFragment(
                    f"world.{fact.subject}.{fact.claim}",
                    (
                        f"{fact.subject} {fact.claim}={fact.value!r} "
                        f"confidence={fact.confidence:.2f}"
                    ),
                    20,
                )
                for fact in world.facts
            ),
            *(
                ContextFragment(
                    f"world.entity.{entity.id}",
                    f"{entity.id} kind={entity.kind} attributes={dict(entity.attributes)!r}",
                    21,
                )
                for entity in world.entities
            ),
            *(
                ContextFragment(
                    f"world.relation.{relation.source}.{relation.relation}.{relation.target}",
                    f"{relation.source} -[{relation.relation}]-> {relation.target}",
                    22,
                )
                for relation in world.relations
            ),
        ]
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

    def _memory_fragments(
        self,
        memories: tuple[MemoryRecord, ...],
    ) -> tuple[ContextFragment, ...]:
        # Priority 40 sits below evidence (30), world (20) and task (10):
        # memory is advisory, so it is the first to be dropped under pressure.
        fragments = (
            ContextFragment(
                f"memory.{record.id}",
                f"{record.kind.value} {record.subject}: {record.content}",
                40,
            )
            for record in sorted(
                memories,
                key=lambda item: (item.confidence, item.created_at, str(item.id)),
                reverse=True,
            )
        )
        return self._budget_fragments(
            fragments,
            max_fragments=self._max_memory_fragments,
            max_characters=self._max_memory_characters,
        )

    def _budget_fragments(
        self,
        fragments: Iterable[ContextFragment],
        *,
        max_fragments: int | None = None,
        max_characters: int | None = None,
    ) -> tuple[ContextFragment, ...]:
        limit_count = max_fragments if max_fragments is not None else self._max_fragments
        limit_chars = max_characters if max_characters is not None else self._max_characters
        selected: list[ContextFragment] = []
        used = 0
        for fragment in fragments:
            if len(selected) >= limit_count:
                break
            remaining = limit_chars - used
            if remaining <= 0:
                break
            content = fragment.content[:remaining]
            selected.append(ContextFragment(fragment.key, content, fragment.priority))
            used += len(content)
        return tuple(selected)
