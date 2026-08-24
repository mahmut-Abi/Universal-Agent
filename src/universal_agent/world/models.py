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
class WorldFactEvidence:
    evidence_id: EvidenceId
    value: JsonValue
    confidence: float
    observed_at: datetime
    source: str


@dataclass(frozen=True, slots=True)
class WorldFactHistory:
    subject: str
    claim: str
    current: WorldFact
    candidates: tuple[WorldFactEvidence, ...]
    conflicting: bool


@dataclass(frozen=True, slots=True)
class WorldEntity:
    id: EntityId
    kind: str
    attributes: JsonMapping = field(default_factory=immutable_json)
    evidence_ids: tuple[EvidenceId, ...] = ()


@dataclass(frozen=True, slots=True)
class WorldRelation:
    source: EntityId
    relation: str
    target: EntityId
    evidence_ids: tuple[EvidenceId, ...] = ()


@dataclass(frozen=True, slots=True)
class WorldNeighborhood:
    root: WorldEntity | None
    facts: tuple[WorldFact, ...] = ()
    outgoing_relations: tuple[WorldRelation, ...] = ()
    incoming_relations: tuple[WorldRelation, ...] = ()
    related_entities: tuple[WorldEntity, ...] = ()


@dataclass(frozen=True, slots=True)
class WorldSnapshot:
    session_id: SessionId
    facts: tuple[WorldFact, ...] = ()
    fact_histories: tuple[WorldFactHistory, ...] = ()
    entities: tuple[WorldEntity, ...] = ()
    relations: tuple[WorldRelation, ...] = ()
    captured_at: datetime = field(default_factory=utc_now)

    def value_for(self, claim: str, *, subject: str | None = None) -> JsonValue:
        for fact in self.facts:
            if fact.claim == claim and (subject is None or fact.subject == subject):
                return fact.value
        return None

    def entity_for(self, entity_id: EntityId | str) -> WorldEntity | None:
        normalized = EntityId(str(entity_id))
        for entity in self.entities:
            if entity.id == normalized:
                return entity
        return None

    def facts_for(
        self,
        subject: EntityId | str,
        *,
        claims: tuple[str, ...] = (),
    ) -> tuple[WorldFact, ...]:
        normalized = str(subject)
        return tuple(
            fact
            for fact in self.facts
            if fact.subject == normalized and (not claims or fact.claim in claims)
        )

    def fact_history_for(self, subject: str, claim: str) -> WorldFactHistory | None:
        for history in self.fact_histories:
            if history.subject == subject and history.claim == claim:
                return history
        return None

    def conflicting_facts(
        self,
        *,
        subject: str | None = None,
        claim: str | None = None,
    ) -> tuple[WorldFactHistory, ...]:
        return tuple(
            history
            for history in self.fact_histories
            if history.conflicting
            and (subject is None or history.subject == subject)
            and (claim is None or history.claim == claim)
        )

    def relations_for(
        self,
        *,
        source: EntityId | str | None = None,
        relation: str | None = None,
        target: EntityId | str | None = None,
    ) -> tuple[WorldRelation, ...]:
        normalized_source = None if source is None else EntityId(str(source))
        normalized_target = None if target is None else EntityId(str(target))
        return tuple(
            item
            for item in self.relations
            if (normalized_source is None or item.source == normalized_source)
            and (relation is None or item.relation == relation)
            and (normalized_target is None or item.target == normalized_target)
        )

    def neighborhood_for(
        self,
        entity_id: EntityId | str,
        *,
        relation: str | None = None,
    ) -> WorldNeighborhood:
        normalized = EntityId(str(entity_id))
        outgoing = self.relations_for(source=normalized, relation=relation)
        incoming = self.relations_for(target=normalized, relation=relation)
        related_ids = tuple(
            dict.fromkeys(
                [
                    *(item.target for item in outgoing),
                    *(item.source for item in incoming),
                ]
            )
        )
        related_entities = tuple(
            entity for item in related_ids if (entity := self.entity_for(item)) is not None
        )
        return WorldNeighborhood(
            self.entity_for(normalized),
            self.facts_for(normalized),
            outgoing,
            incoming,
            related_entities,
        )


class WorldUpdater(Protocol):
    @property
    def name(self) -> str: ...

    def apply(self, model: WorldModel, evidence: Evidence) -> bool: ...


class WorldModel(Protocol):
    def apply_fact(self, evidence: Evidence) -> bool: ...

    def apply_entity(self, session_id: SessionId, entity: WorldEntity) -> bool: ...

    def apply_relation(self, session_id: SessionId, relation: WorldRelation) -> bool: ...

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
