from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType
from uuid import uuid4

from universal_agent.core import (
    ActionId,
    ErrorCode,
    JsonMapping,
    JsonValue,
    ObservationId,
    ObservationStatus,
    SessionId,
    TaskId,
    ToolCall,
    ToolResult,
    immutable_json,
    runtime_primitives,
)
from universal_agent.core.config_validation import parse_non_empty_string, parse_positive_int
from universal_agent.evidence import Evidence, EvidenceId
from universal_agent.world.models import (
    EntityId,
    WorldEntity,
    WorldFact,
    WorldFactEvidence,
    WorldFactHistory,
    WorldRelation,
    WorldSnapshot,
    WorldUpdater,
)


@dataclass(slots=True)
class DeterministicClock:
    """Step a timezone-aware clock forward on every runtime timestamp request."""

    start: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    step: timedelta = timedelta(seconds=1)
    tick: int = 0

    def __post_init__(self) -> None:
        if self.start.tzinfo is None:
            raise ValueError("deterministic clock start must be timezone-aware")
        if self.step < timedelta(0):
            raise ValueError("deterministic clock step must be non-negative")

    def now(self) -> datetime:
        value = self.start + (self.step * self.tick)
        self.tick += 1
        return value


@dataclass(slots=True)
class DeterministicIdFactory:
    """Generate stable runtime IDs per prefix for deterministic tests."""

    width: int = 4
    counters: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        parse_positive_int(self.width, "deterministic id width")

    def new_id(self, prefix: str) -> str:
        parse_non_empty_string(prefix, "deterministic id prefix")
        next_value = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = next_value
        return f"{prefix}-{next_value:0{self.width}d}"


@dataclass(slots=True)
class ToolResultScript:
    """A scripted tool result for a specific tool call."""

    tool_name: str
    capability: str
    status: ObservationStatus = ObservationStatus.SUCCEEDED
    output: JsonMapping = field(default_factory=dict)
    error: str = ""
    error_code: ErrorCode | None = None


class MockToolRuntime:
    """Deterministic tool runtime that returns scripted responses.

    Usage:
        mock = MockToolRuntime([
            ToolResultScript("kubectl", "get_pod", output={"status": "Running"}),
            ToolResultScript("kubectl", "get_logs", output={"logs": "ok"}),
        ])
        result = await mock.execute(tool_call)
    """

    def __init__(
        self,
        scripts: Iterable[ToolResultScript] = (),
        *,
        default_status: ObservationStatus = ObservationStatus.SUCCEEDED,
        default_output: JsonMapping | None = None,
    ) -> None:
        self._scripts: deque[ToolResultScript] = deque(scripts)
        self._default_status = default_status
        self._default_output: JsonMapping = default_output or {}
        self._calls: list[ToolCall] = []

    async def execute(self, call: ToolCall) -> ToolResult:
        self._calls.append(call)
        if not self._scripts:
            is_succeeded = self._default_status == ObservationStatus.SUCCEEDED
            return ToolResult(
                status=self._default_status,
                output=immutable_json(self._default_output) if is_succeeded else {},
                error="" if is_succeeded else "no scripted response",
                error_code=None if is_succeeded else ErrorCode.TOOL_FAILURE,
            )
        script = self._scripts.popleft()
        if script.status == ObservationStatus.SUCCEEDED:
            return ToolResult(
                status=ObservationStatus.SUCCEEDED,
                output=immutable_json(script.output),
            )
        return ToolResult(
            status=script.status,
            error=script.error,
            error_code=script.error_code,
        )

    @property
    def calls(self) -> list[ToolCall]:
        return list(self._calls)

    @property
    def call_count(self) -> int:
        return len(self._calls)


class MockWorldModel:
    """Deterministic world model that can be pre-populated with initial state.

    Usage:
        mock = MockWorldModel()
        mock.seed_fact(session_id, "pod-1", "status", "Running", confidence=0.9)
        mock.seed_entity(session_id, EntityId("pod-1"), "Pod", {"name": "pod-1"})
        snapshot = mock.snapshot(session_id)
    """

    def __init__(self) -> None:
        self._facts: dict[tuple[SessionId, str, str], list[Evidence]] = {}
        self._entities: dict[tuple[SessionId, EntityId], WorldEntity] = {}
        self._relations: dict[
            tuple[SessionId, EntityId, str, EntityId],
            WorldRelation,
        ] = {}
        self._snapshot_cache: dict[SessionId, WorldSnapshot] = {}

    def seed_fact(
        self,
        session_id: SessionId,
        subject: str,
        claim: str,
        value: JsonValue,
        *,
        confidence: float = 1.0,
        source: str = "test",
        task_id: TaskId | None = None,
        action_id: ActionId | None = None,
        observation_id: ObservationId | None = None,
        evidence_id: EvidenceId | None = None,
    ) -> Evidence:
        evidence = Evidence(
            id=evidence_id or EvidenceId(f"ev-{subject}-{claim}-{uuid4().hex[:8]}"),
            session_id=session_id,
            task_id=task_id or TaskId(""),
            action_id=action_id or ActionId(""),
            observation_id=observation_id or ObservationId(""),
            subject=subject,
            claim=claim,
            value=value,
            confidence=confidence,
            source=source,
        )
        key = (session_id, subject, claim)
        values = self._facts.setdefault(key, [])
        if any(item.id == evidence.id for item in values):
            return evidence
        values.append(evidence)
        self._snapshot_cache.pop(session_id, None)
        return evidence

    def seed_entity(
        self,
        session_id: SessionId,
        entity_id: EntityId,
        kind: str,
        attributes: JsonMapping | None = None,
    ) -> WorldEntity:
        entity = WorldEntity(entity_id, kind, immutable_json(attributes or {}))
        key = (session_id, entity_id)
        self._entities[key] = entity
        self._snapshot_cache.pop(session_id, None)
        return entity

    def seed_relation(
        self,
        session_id: SessionId,
        source: EntityId,
        relation: str,
        target: EntityId,
    ) -> WorldRelation:
        rel = WorldRelation(source, relation, target)
        key = (session_id, source, relation, target)
        self._relations[key] = rel
        self._snapshot_cache.pop(session_id, None)
        return rel

    def apply_fact(self, evidence: Evidence) -> bool:
        key = (evidence.session_id, evidence.subject, evidence.claim)
        values = self._facts.setdefault(key, [])
        if any(item.id == evidence.id for item in values):
            return False
        values.append(evidence)
        self._snapshot_cache.pop(evidence.session_id, None)
        return True

    def apply_entity(self, session_id: SessionId, entity: WorldEntity) -> bool:
        key = (session_id, entity.id)
        existing = self._entities.get(key)
        if existing == entity:
            return False
        self._entities[key] = entity
        self._snapshot_cache.pop(session_id, None)
        return True

    def apply_relation(self, session_id: SessionId, relation: WorldRelation) -> bool:
        key = (session_id, relation.source, relation.relation, relation.target)
        existing = self._relations.get(key)
        if existing == relation:
            return False
        self._relations[key] = relation
        self._snapshot_cache.pop(session_id, None)
        return True

    def forget(self, session_id: SessionId) -> None:
        self._facts = {key: value for key, value in self._facts.items() if key[0] != session_id}
        self._entities = {
            key: value for key, value in self._entities.items() if key[0] != session_id
        }
        self._relations = {
            key: value for key, value in self._relations.items() if key[0] != session_id
        }
        self._snapshot_cache.pop(session_id, None)

    def rebuild(
        self,
        session_id: SessionId,
        evidence: Iterable[Evidence],
        updaters: tuple[WorldUpdater, ...],
    ) -> None:
        if not updaters:
            raise ValueError("world rebuild requires at least one updater")
        self.forget(session_id)
        ordered = sorted(
            (item for item in evidence if item.session_id == session_id),
            key=lambda item: (item.observed_at, str(item.id)),
        )
        for item in ordered:
            for updater in updaters:
                updater.apply(self, item)

    def snapshot(
        self,
        session_id: SessionId,
        *,
        subjects: tuple[str, ...] = (),
        claims: tuple[str, ...] = (),
    ) -> WorldSnapshot:
        if not subjects and not claims and session_id in self._snapshot_cache:
            return self._snapshot_cache[session_id]

        facts: list[WorldFact] = []
        histories: list[WorldFactHistory] = []
        for (stored_session, subject, claim), evidence in self._facts.items():
            if stored_session != session_id:
                continue
            if subjects and subject not in subjects:
                continue
            if claims and claim not in claims:
                continue
            ordered_evidence = tuple(
                sorted(evidence, key=lambda item: (item.observed_at, str(item.id)))
            )
            current = max(
                evidence,
                key=lambda item: (item.confidence, item.observed_at, str(item.id)),
            )
            fact = WorldFact(
                subject,
                claim,
                current.value,
                current.confidence,
                current.observed_at,
                tuple(item.id for item in ordered_evidence),
            )
            facts.append(fact)
            histories.append(
                WorldFactHistory(
                    subject,
                    claim,
                    fact,
                    tuple(
                        WorldFactEvidence(
                            item.id,
                            item.value,
                            item.confidence,
                            item.observed_at,
                            item.source,
                        )
                        for item in ordered_evidence
                    ),
                    len({_value_key(item.value) for item in evidence}) > 1,
                )
            )
        facts.sort(key=lambda item: (item.subject, item.claim))
        histories.sort(key=lambda item: (item.subject, item.claim))
        entities = tuple(
            sorted(
                (
                    entity
                    for (stored_session, _), entity in self._entities.items()
                    if stored_session == session_id and (not subjects or str(entity.id) in subjects)
                ),
                key=lambda item: str(item.id),
            )
        )
        relations = tuple(
            sorted(
                (
                    relation
                    for (stored_session, _, _, _), relation in self._relations.items()
                    if stored_session == session_id
                    and (not subjects or str(relation.source) in subjects)
                ),
                key=lambda item: (str(item.source), item.relation, str(item.target)),
            )
        )
        result = WorldSnapshot(
            session_id,
            facts=tuple(facts),
            fact_histories=tuple(histories),
            entities=entities,
            relations=relations,
        )
        if not subjects and not claims:
            self._snapshot_cache[session_id] = result
        return result


def _value_key(value: object) -> str:
    from universal_agent.core import dumps_json

    return dumps_json(value)


@dataclass(slots=True)
class DeterministicRuntimeMode:
    """Temporarily install deterministic runtime primitives for evaluation tests.

    Provides:
    - DeterministicClock: step-based clock
    - DeterministicIdFactory: stable IDs per prefix
    - MockToolRuntime: scripted tool responses
    - MockWorldModel: pre-populatable world state
    """

    clock: DeterministicClock = field(default_factory=DeterministicClock)
    ids: DeterministicIdFactory = field(default_factory=DeterministicIdFactory)
    tool_runtime: MockToolRuntime = field(default_factory=MockToolRuntime)
    world_model: MockWorldModel = field(default_factory=MockWorldModel)
    _context: AbstractContextManager[None] | None = field(default=None, init=False)

    def __enter__(self) -> DeterministicRuntimeMode:
        if self._context is not None:
            raise RuntimeError("deterministic runtime mode is already active")
        self._context = runtime_primitives(clock=self.clock.now, id_factory=self.ids.new_id)
        self._context.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._context is None:
            return
        self._context.__exit__(exc_type, exc, traceback)
        self._context = None
