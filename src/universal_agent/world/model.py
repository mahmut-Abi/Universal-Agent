from __future__ import annotations

from collections.abc import Iterable, Mapping

from universal_agent.core import JsonMapping, JsonValue, SessionId, dumps_json, immutable_json
from universal_agent.evidence import Evidence, EvidenceId
from universal_agent.world.models import (
    EntityId,
    WorldEntity,
    WorldFact,
    WorldFactEvidence,
    WorldFactHistory,
    WorldModel,
    WorldRelation,
    WorldSnapshot,
    WorldUpdater,
)


class FactWorldUpdater:
    name = "facts"

    def apply(self, model: WorldModel, evidence: Evidence) -> bool:
        changed = model.apply_fact(evidence)
        entity = _entity_from_evidence(evidence)
        if entity is not None:
            changed = model.apply_entity(evidence.session_id, entity) or changed
        for relation in _relations_from_evidence(evidence):
            changed = model.apply_relation(evidence.session_id, relation) or changed
        return changed


class InMemoryWorldModel:
    def __init__(self) -> None:
        self._facts: dict[tuple[SessionId, str, str], list[Evidence]] = {}
        self._entities: dict[tuple[SessionId, EntityId], WorldEntity] = {}
        self._relations: dict[
            tuple[SessionId, EntityId, str, EntityId],
            WorldRelation,
        ] = {}

    def apply_fact(self, evidence: Evidence) -> bool:
        key = (evidence.session_id, evidence.subject, evidence.claim)
        values = self._facts.setdefault(key, [])
        if any(item.id == evidence.id for item in values):
            return False
        values.append(evidence)
        self._refresh_entity_attributes(evidence.session_id, EntityId(evidence.subject))
        return True

    def apply_entity(self, session_id: SessionId, entity: WorldEntity) -> bool:
        if not entity.kind.strip():
            raise ValueError("world entity kind must not be empty")
        key = (session_id, entity.id)
        existing = self._entities.get(key)
        kind = self._kind_for_subject(session_id, entity.id) or entity.kind
        merged_attributes = _merge_json_mappings(
            self._fact_attributes(session_id, entity.id),
            existing.attributes if existing is not None else immutable_json(),
            entity.attributes,
        )
        merged_evidence_ids = _dedupe_evidence_ids(
            *(existing.evidence_ids if existing is not None else ()),
            *entity.evidence_ids,
        )
        merged = WorldEntity(entity.id, kind, merged_attributes, merged_evidence_ids)
        if existing == merged:
            return False
        self._entities[key] = merged
        return True

    def apply_relation(self, session_id: SessionId, relation: WorldRelation) -> bool:
        if not relation.relation.strip():
            raise ValueError("world relation must not be empty")
        key = (session_id, relation.source, relation.relation, relation.target)
        existing = self._relations.get(key)
        if existing is None:
            self._relations[key] = relation
            return True
        merged = WorldRelation(
            relation.source,
            relation.relation,
            relation.target,
            _dedupe_evidence_ids(*existing.evidence_ids, *relation.evidence_ids),
        )
        if existing == merged:
            return False
        self._relations[key] = merged
        return True

    def forget(self, session_id: SessionId) -> None:
        self._facts = {key: value for key, value in self._facts.items() if key[0] != session_id}
        self._entities = {
            key: value for key, value in self._entities.items() if key[0] != session_id
        }
        self._relations = {
            key: value for key, value in self._relations.items() if key[0] != session_id
        }

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
                sorted(
                    evidence,
                    key=lambda item: (item.observed_at, str(item.id)),
                )
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
                    _has_conflicting_values(ordered_evidence),
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
        return WorldSnapshot(
            session_id,
            facts=tuple(facts),
            fact_histories=tuple(histories),
            entities=entities,
            relations=relations,
        )

    def _refresh_entity_attributes(self, session_id: SessionId, entity_id: EntityId) -> None:
        key = (session_id, entity_id)
        existing = self._entities.get(key)
        if existing is None:
            return
        attributes = _merge_json_mappings(
            self._fact_attributes(session_id, entity_id),
            existing.attributes,
        )
        updated = WorldEntity(entity_id, existing.kind, attributes, existing.evidence_ids)
        if updated != existing:
            self._entities[key] = updated

    def _fact_attributes(self, session_id: SessionId, entity_id: EntityId) -> JsonMapping:
        attributes: dict[str, JsonValue] = {}
        subject = str(entity_id)
        for (stored_session, stored_subject, claim), evidence in self._facts.items():
            if (
                stored_session != session_id
                or stored_subject != subject
                or claim == "kind"
                or claim.startswith("relation:")
            ):
                continue
            current = _current_fact(evidence)
            attributes[claim] = current.value
        return immutable_json(attributes)

    def _kind_for_subject(self, session_id: SessionId, entity_id: EntityId) -> str | None:
        evidence = self._facts.get((session_id, str(entity_id), "kind"))
        if not evidence:
            return None
        current = _current_fact(evidence)
        if isinstance(current.value, str) and current.value.strip():
            return current.value.strip()
        return None


def _current_fact(evidence: list[Evidence]) -> Evidence:
    return max(evidence, key=lambda item: (item.confidence, item.observed_at, str(item.id)))


def _entity_from_evidence(evidence: Evidence) -> WorldEntity | None:
    if evidence.claim != "kind":
        return None
    if not isinstance(evidence.value, str) or not evidence.value.strip():
        return None
    return WorldEntity(
        EntityId(evidence.subject),
        evidence.value.strip(),
        evidence_ids=(evidence.id,),
    )


def _relations_from_evidence(evidence: Evidence) -> tuple[WorldRelation, ...]:
    prefix = "relation:"
    if not evidence.claim.startswith(prefix):
        return ()
    relation = evidence.claim[len(prefix) :].strip()
    if not relation:
        return ()
    targets = _relation_targets(evidence.value)
    return tuple(
        WorldRelation(
            EntityId(evidence.subject),
            relation,
            EntityId(target),
            (evidence.id,),
        )
        for target in targets
    )


def _relation_targets(value: JsonValue) -> tuple[str, ...]:
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    if isinstance(value, list):
        targets: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                targets.append(item.strip())
        return tuple(targets)
    return ()


def _merge_json_mappings(*items: JsonMapping) -> JsonMapping:
    merged: dict[str, JsonValue] = {}
    for item in items:
        merged.update(item)
    return immutable_json(merged)


def _dedupe_evidence_ids(*items: EvidenceId) -> tuple[EvidenceId, ...]:
    return tuple(dict.fromkeys(items))


def _has_conflicting_values(evidence: tuple[Evidence, ...]) -> bool:
    return len({_value_key(item.value) for item in evidence}) > 1


def _value_key(value: JsonValue) -> str:
    return dumps_json(_plain_json_value(value))


def _plain_json_value(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _plain_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_json_value(item) for item in value]
    return value
