from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
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


class WorldRelationDirection(StrEnum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class WorldNeighborhood:
    root: WorldEntity | None
    facts: tuple[WorldFact, ...] = ()
    outgoing_relations: tuple[WorldRelation, ...] = ()
    incoming_relations: tuple[WorldRelation, ...] = ()
    related_entities: tuple[WorldEntity, ...] = ()


@dataclass(frozen=True, slots=True)
class WorldGraphNode:
    entity_id: EntityId
    depth: int
    entity: WorldEntity | None = None


@dataclass(frozen=True, slots=True)
class WorldGraph:
    root: WorldEntity | None
    nodes: tuple[WorldGraphNode, ...] = ()
    relations: tuple[WorldRelation, ...] = ()
    entities: tuple[WorldEntity, ...] = ()


@dataclass(frozen=True, slots=True)
class WorldGraphQuery:
    max_depth: int = 1
    relations: tuple[str, ...] = ()
    direction: WorldRelationDirection | str = WorldRelationDirection.BOTH
    entity_kinds: tuple[str, ...] = ()
    required_facts: JsonMapping = field(default_factory=immutable_json)


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

    def relation_graph_for(
        self,
        entity_id: EntityId | str,
        *,
        max_depth: int = 1,
        relations: tuple[str, ...] = (),
        direction: WorldRelationDirection | str = WorldRelationDirection.BOTH,
        query: WorldGraphQuery | None = None,
    ) -> WorldGraph:
        graph_query = query or WorldGraphQuery(max_depth, relations, direction)
        if graph_query.max_depth < 0:
            raise ValueError("world relation graph max_depth must be >= 0")
        try:
            normalized_direction = WorldRelationDirection(graph_query.direction)
        except ValueError as exc:
            raise ValueError(
                "world relation graph direction must be one of: outgoing, incoming, both"
            ) from exc

        root_id = EntityId(str(entity_id))
        root = self.entity_for(root_id)
        if root is None:
            return WorldGraph(root=None)

        relation_filter = frozenset(graph_query.relations)
        depth_by_id: dict[EntityId, int] = {root_id: 0}
        frontier: tuple[EntityId, ...] = (root_id,)
        traversed: dict[tuple[EntityId, str, EntityId], WorldRelation] = {}

        for depth in range(graph_query.max_depth):
            next_frontier: list[EntityId] = []
            for current in frontier:
                for world_relation in self._graph_relations_for(
                    current,
                    relation_filter=relation_filter,
                    direction=normalized_direction,
                ):
                    relation_key = (
                        world_relation.source,
                        world_relation.relation,
                        world_relation.target,
                    )
                    traversed.setdefault(relation_key, world_relation)
                    related_id = (
                        world_relation.target
                        if world_relation.source == current
                        else world_relation.source
                    )
                    if related_id in depth_by_id:
                        continue
                    depth_by_id[related_id] = depth + 1
                    next_frontier.append(related_id)
            frontier = tuple(dict.fromkeys(next_frontier))
            if not frontier:
                break

        candidate_nodes = tuple(
            WorldGraphNode(entity_id, depth, self.entity_for(entity_id))
            for entity_id, depth in sorted(
                depth_by_id.items(),
                key=lambda item: (item[1], str(item[0])),
            )
        )
        nodes = tuple(
            node
            for node in candidate_nodes
            if node.entity_id == root_id or self._graph_node_matches_query(node, graph_query)
        )
        included_ids = frozenset(node.entity_id for node in nodes)
        graph_relations = tuple(
            relation
            for relation in sorted(
                traversed.values(),
                key=lambda item: (str(item.source), item.relation, str(item.target)),
            )
            if relation.source in included_ids and relation.target in included_ids
        )
        entities = tuple(node.entity for node in nodes if node.entity is not None)
        return WorldGraph(
            root,
            nodes,
            graph_relations,
            entities,
        )

    def _graph_relations_for(
        self,
        entity_id: EntityId,
        *,
        relation_filter: frozenset[str],
        direction: WorldRelationDirection,
    ) -> tuple[WorldRelation, ...]:
        selected: list[WorldRelation] = []
        if direction in {WorldRelationDirection.OUTGOING, WorldRelationDirection.BOTH}:
            selected.extend(self.relations_for(source=entity_id))
        if direction in {WorldRelationDirection.INCOMING, WorldRelationDirection.BOTH}:
            selected.extend(self.relations_for(target=entity_id))
        return tuple(
            item for item in selected if not relation_filter or item.relation in relation_filter
        )

    def _graph_node_matches_query(
        self,
        node: WorldGraphNode,
        query: WorldGraphQuery,
    ) -> bool:
        if not query.entity_kinds and not query.required_facts:
            return True
        if node.entity is None:
            return False
        if query.entity_kinds and node.entity.kind not in query.entity_kinds:
            return False
        return all(
            self.value_for(claim, subject=str(node.entity_id)) == expected
            for claim, expected in query.required_facts.items()
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
