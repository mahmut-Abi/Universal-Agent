from __future__ import annotations

import heapq
import json
import re
from collections.abc import Iterable
from typing import Protocol

from universal_agent.core import (
    AgentState,
    CapabilityDefinition,
    CapabilityInputContract,
    CapabilitySummary,
    ContextFragment,
    DecisionContext,
    dumps_json,
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
        capability_input_contracts: tuple[CapabilityInputContract, ...] = (),
    ) -> DecisionContext: ...


class BasicContextCompiler:
    def __init__(
        self,
        *,
        max_fragments: int = 8,
        max_characters: int = 4_000,
        max_memory_fragments: int = 4,
        max_memory_characters: int = 1_200,
        max_fragment_characters: int = 2_000,
        enable_relevance_ranking: bool = True,
        enable_compression: bool = True,
        enable_dedup: bool = False,
        stall_repeats: int = 3,
    ) -> None:
        self._max_fragments = max_fragments
        self._max_characters = max_characters
        self._max_memory_fragments = max_memory_fragments
        self._max_memory_characters = max_memory_characters
        self._max_fragment_characters = max_fragment_characters
        self._enable_relevance_ranking = enable_relevance_ranking
        self._enable_compression = enable_compression
        self._enable_dedup = enable_dedup
        self._stall_repeats = stall_repeats

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
        capability_input_contracts: tuple[CapabilityInputContract, ...] = (),
    ) -> DecisionContext:
        state_tokens: set[str] | None = None
        if self._enable_relevance_ranking:
            state_tokens = self._compute_state_tokens(state)
        fragments = self._select_fragments(state, providers, state_tokens)
        contracts = {item.capability: item for item in capability_input_contracts}
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
                self._capability_summary(item, contracts.get(item.name)) for item in capabilities
            ),
            goal_success_criteria=state.goal.success_criteria,
            current_task_required_criteria=state.current_task.required_criteria,
            domain_context=(*fragments, *self._stall_advisory(evidence)),
            world_context=self._world_fragments(world, state_tokens),
            evidence_context=self._evidence_fragments(evidence, state_tokens),
            task_context=self._task_fragments(tasks, state_tokens),
            memory_context=self._memory_fragments(memories, state_tokens),
            policy_summary=policy_summary,
        )

    def _compute_state_tokens(self, state: AgentState) -> set[str]:
        """Pre-compute tokens from goal, task, and criteria for relevance ranking."""
        tokens = self._tokens(state.goal.description)
        tokens |= self._tokens(state.current_task.description)
        tokens |= {self._token(c) for c in state.current_task.required_criteria}
        tokens |= {self._token(c) for c in state.satisfied_criteria}
        return tokens

    def _capability_summary(
        self,
        capability: CapabilityDefinition,
        contract: CapabilityInputContract | None,
    ) -> CapabilitySummary:
        return CapabilitySummary(
            capability.name,
            capability.description,
            capability.category,
            capability.risk,
            required_arguments=() if contract is None else contract.required_arguments,
            argument_schema=immutable_json() if contract is None else contract.argument_schema,
        )

    def _select_fragments(
        self,
        state: AgentState,
        providers: tuple[DomainContextProvider, ...],
        state_tokens: set[str] | None,
    ) -> tuple[ContextFragment, ...]:
        unique: dict[str, ContextFragment] = {}
        for provider in providers:
            for fragment in provider.provide(state):
                current = unique.get(fragment.key)
                if current is None or fragment.priority < current.priority:
                    unique[fragment.key] = fragment
        return self._budget_fragments(tuple(unique.values()), state_tokens=state_tokens)

    def _world_fragments(
        self, world: WorldSnapshot | None, state_tokens: set[str] | None
    ) -> tuple[ContextFragment, ...]:
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
        return self._budget_fragments(fragments, state_tokens=state_tokens)

    def _stall_advisory(
        self,
        evidence: tuple[Evidence, ...],
    ) -> tuple[ContextFragment, ...]:
        """Advise the model when repeated identical observations stall a goal.

        A stall means the most recent observations all came from the same
        capability and produced no new distinct (subject, claim, value) fact.
        Continuing the same inspection cannot make progress, so the advisory
        tells the model to change approach, use a remediation capability, or
        finish with an explicit reason instead of looping.
        """
        if self._stall_repeats < 1:
            return ()
        distinct_facts: set[tuple[str, str, str]] = set()
        recent_sources: list[str] = []
        for item in sorted(
            evidence,
            key=lambda item: (item.observed_at, str(item.id)),
        )[-self._stall_repeats :]:
            if len(recent_sources) < self._stall_repeats:
                recent_sources.append(item.source.split(":", 1)[0])
            distinct_facts.add((item.subject, item.claim, dumps_json(item.value)))
        if (
            len(recent_sources) < self._stall_repeats
            or len(set(recent_sources)) != 1
            or len(distinct_facts) > 1
        ):
            return ()
        capability = recent_sources[0]
        return (
            ContextFragment(
                "runtime.stall_advisory",
                f"Decision stall detected: {self._stall_repeats} consecutive "
                f"observations from '{capability}' produced no new facts. Repeating "
                "this inspection cannot satisfy the goal. Change approach: use a "
                "different capability (for example a remediation action), or finish "
                "with an explicit report of the findings and why the goal cannot "
                "be completed.",
                1,
            ),
        )

    def _evidence_fragments(
        self,
        evidence: tuple[Evidence, ...],
        state_tokens: set[str] | None,
    ) -> tuple[ContextFragment, ...]:
        # nlargest keeps the same ordering as a full sort followed by
        # truncation to max_fragments, but is O(n log k) instead of O(n log n).
        top = heapq.nlargest(
            self._max_fragments,
            evidence,
            key=lambda item: (item.confidence, item.observed_at, str(item.id)),
        )
        fragments = (
            ContextFragment(
                f"evidence.{item.id}",
                f"{item.subject} {item.claim}={item.value!r} source={item.source}",
                30,
            )
            for item in top
        )
        return self._budget_fragments(
            fragments,
            state_tokens=state_tokens,
            pre_sorted=True,
        )

    def _task_fragments(
        self, tasks: TaskManager | None, state_tokens: set[str] | None
    ) -> tuple[ContextFragment, ...]:
        if tasks is None:
            return ()
        return self._budget_fragments(
            (
                ContextFragment(
                    f"task.{task.id}",
                    f"{task.description} status={task.status.value}",
                    10,
                )
                for task in tasks.all()
            ),
            state_tokens=state_tokens,
        )

    def _memory_fragments(
        self,
        memories: tuple[MemoryRecord, ...],
        state_tokens: set[str] | None,
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
            state_tokens=state_tokens,
            max_fragments=self._max_memory_fragments,
            max_characters=self._max_memory_characters,
        )

    def _budget_fragments(
        self,
        fragments: Iterable[ContextFragment],
        *,
        state_tokens: set[str] | None,
        max_fragments: int | None = None,
        max_characters: int | None = None,
        pre_sorted: bool = False,
    ) -> tuple[ContextFragment, ...]:
        limit_count = max_fragments if max_fragments is not None else self._max_fragments
        limit_chars = max_characters if max_characters is not None else self._max_characters

        fragments_list = list(fragments)

        if not pre_sorted:
            if state_tokens:
                fragments_list.sort(
                    key=lambda f: (
                        f.priority,
                        -self._relevance(f, state_tokens),
                        f.key,
                    )
                )
            else:
                fragments_list.sort(key=lambda f: (f.priority, f.key))

        seen_content: set[str] = set()
        selected: list[ContextFragment] = []
        used = 0

        for fragment in fragments_list:
            if len(selected) >= limit_count:
                break

            norm = self._normalize(fragment.content)
            if self._enable_dedup and norm in seen_content:
                continue
            seen_content.add(norm)

            remaining = limit_chars - used
            if remaining <= 0:
                break

            content = fragment.content
            if self._enable_compression and len(content) > self._max_fragment_characters:
                content = self._compress(content, self._max_fragment_characters)
            content = content[:remaining]

            selected.append(ContextFragment(fragment.key, content, fragment.priority))
            used += len(content)

        return tuple(selected)

    def _tokens(self, text: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9_]+", text.lower()) if w}

    def _relevance(self, fragment: ContextFragment, state_tokens: set[str]) -> float:
        if not state_tokens:
            return 0.0
        frag_tokens = self._tokens(fragment.content) | self._tokens(fragment.key)
        overlap_count = len(state_tokens & frag_tokens)
        return overlap_count / len(state_tokens)

    def _token(self, text: str) -> str:
        return text.lower()

    def _normalize(self, content: str) -> str:
        return content.strip().lower()

    def _compress(self, content: str, limit: int) -> str:
        if len(content) <= limit:
            return content
        if limit <= 3:
            return content[:limit]
        if self._enable_compression and self._looks_like_json(content):
            compressed = self._compress_json(content, limit)
            if compressed is not None:
                return compressed
        head = limit // 2
        tail = limit - head - 1
        if tail <= 0:
            return content[:limit]
        return content[:head] + "\u2026" + content[-tail:]

    @staticmethod
    def _looks_like_json(content: str) -> bool:
        stripped = content.lstrip()
        return stripped.startswith(("{", "["))

    def _compress_json(self, content: str, limit: int) -> str | None:
        """Shrink JSON content by dropping whole entries, never mid-token.

        Character-level truncation of JSON produces invalid structure that the
        model may fail to parse. Instead, drop the last entries of containers
        (annotated with a truncation marker) until the payload fits the
        budget. Returns None when even the skeleton cannot fit, so the caller
        falls back to plain truncation.
        """
        try:
            data = json.loads(content)
        except ValueError:
            return None
        data = self._cap_json_collections(data)
        while True:
            rendered = dumps_json(data)
            if len(rendered) <= limit:
                return rendered
            if not isinstance(data, dict):
                return None
            # Only drop real keys: dropping the truncation marker itself and
            # re-adding it would not shrink the payload (infinite loop).
            real_keys = [key for key in data if key != "__truncated_keys__"]
            if not real_keys:
                return None
            remaining = dict(data)
            del remaining[real_keys[-1]]
            if remaining:
                remaining["__truncated_keys__"] = remaining.get("__truncated_keys__", 0) + 1
            data = remaining

    def _cap_json_collections(self, node: object, max_items: int = 6) -> object:
        if isinstance(node, dict):
            trimmed = {k: self._cap_json_collections(v, max_items) for k, v in node.items()}
            if len(trimmed) > max_items:
                kept = dict(list(trimmed.items())[:max_items])
                kept["__truncated_keys__"] = len(trimmed) - max_items
                return kept
            return trimmed
        if isinstance(node, list):
            trimmed_list = [self._cap_json_collections(v, max_items) for v in node]
            if len(trimmed_list) > max_items:
                kept_list: list[object] = trimmed_list[:max_items]
                kept_list.append({"__truncated_items__": len(trimmed_list) - max_items})
                return kept_list
            return trimmed_list
        return node
