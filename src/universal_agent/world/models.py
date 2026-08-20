from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import NewType, Protocol

from universal_agent.core import JsonMapping, JsonValue, SessionId, immutable_json, utc_now
from universal_agent.evidence import Evidence, EvidenceId

EntityId = NewType("EntityId", str)


@dataclass(frozen=True, slots=True)
class WorldFact:
    subject: str
    claim: str
    value: JsonValue
    confidence: float
    observed_at: datetime
    evidence_ids: tuple[EvidenceId, ...]


@dataclass(frozen=True, slots=True)
class WorldEntity:
    id: EntityId
    kind: str
    attributes: JsonMapping = field(default_factory=immutable_json)


@dataclass(frozen=True, slots=True)
class WorldRelation:
    source: EntityId
    relation: str
    target: EntityId
    evidence_ids: tuple[EvidenceId, ...] = ()


@dataclass(frozen=True, slots=True)
class WorldSnapshot:
    session_id: SessionId
    facts: tuple[WorldFact, ...] = ()
    entities: tuple[WorldEntity, ...] = ()
    relations: tuple[WorldRelation, ...] = ()
    captured_at: datetime = field(default_factory=utc_now)

    def value_for(self, claim: str, *, subject: str | None = None) -> JsonValue:
        for fact in self.facts:
            if fact.claim == claim and (subject is None or fact.subject == subject):
                return fact.value
        return None


class WorldUpdater(Protocol):
    @property
    def name(self) -> str: ...

    def apply(self, model: WorldModel, evidence: Evidence) -> bool: ...


class WorldModel(Protocol):
    def apply_fact(self, evidence: Evidence) -> bool: ...

    def forget(self, session_id: SessionId) -> None: ...

    def rebuild(
        self,
        session_id: SessionId,
        evidence: Iterable[Evidence],
        updaters: tuple[WorldUpdater, ...],
    ) -> None: ...

    def snapshot(
        self,
        session_id: SessionId,
        *,
        subjects: tuple[str, ...] = (),
        claims: tuple[str, ...] = (),
    ) -> WorldSnapshot: ...
