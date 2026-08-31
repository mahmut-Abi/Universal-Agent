from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from universal_agent.core import (
    JsonMapping,
    JsonValue,
    SessionId,
    immutable_json,
    utc_now,
)
from universal_agent.evidence import Evidence, EvidenceQuery, EvidenceStore
from universal_agent.world.cross_domain import (
    CrossDomainConflict,
    DomainWorldView,
    EntityIdentityMapping,
    FactDomainSource,
    WorldMergePolicy,
    merge_world_views,
)
from universal_agent.world.models import (
    EntityId,
    WorldEntity,
    WorldFact,
    WorldModel,
    WorldRelation,
    WorldSnapshot,
)


@dataclass(frozen=True, slots=True)
class MergedWorldResult:
    """Result of cross-domain world merging."""

    snapshot: WorldSnapshot
    conflicts: tuple[CrossDomainConflict, ...]
    fact_sources: tuple[FactDomainSource, ...]
    identity_mappings: tuple[EntityIdentityMapping, ...] = ()


class CrossDomainWorldModel:
    """Wraps a WorldModel to provide cross-domain merged snapshots.

    When multiple domains are active, this model rebuilds per-domain snapshots
    from evidence and merges them using the cross-domain merge logic, detecting
    conflicts where different domains report different values for the same fact.
    """

    def __init__(
        self,
        base_model: WorldModel,
        evidence_store: EvidenceStore,
    ) -> None:
        self._base_model = base_model
        self._evidence_store = evidence_store

    def merged_snapshot(
        self,
        session_id: SessionId,
        *,
        subjects: tuple[str, ...] = (),
        claims: tuple[str, ...] = (),
        merge_policy: WorldMergePolicy | None = None,
    ) -> MergedWorldResult:
        """Build a merged world snapshot from all domain evidence.

        Args:
            session_id: Session to query.
            subjects: Optional subject filter.
            claims: Optional claim filter.

        Returns:
            MergedWorldResult with merged snapshot, conflicts, and fact sources.
        """
        # The evidence store supports scalar filters. Apply plural filters
        # here so a request for several subjects/claims cannot narrow to only
        # the first item.
        all_evidence = self._evidence_store.query(
            EvidenceQuery(
                session_id=session_id,
                task_id=None,
                subject=None,
                claim=None,
                limit=None,
            )
        )
        subject_set = set(subjects)
        claim_set = set(claims)
        all_evidence = tuple(
            item
            for item in all_evidence
            if (not subject_set or item.subject in subject_set)
            and (not claim_set or item.claim in claim_set)
        )

        # Group evidence by domain
        domain_evidence: dict[tuple[str, str], list[Evidence]] = defaultdict(list)
        for ev in all_evidence:
            domain_key = (ev.domain_name or "default", ev.domain_version or "1.0.0")
            domain_evidence[domain_key].append(ev)

        # Build DomainWorldView for each domain
        views: list[DomainWorldView] = []
        for (domain_name, domain_version), ev_list in domain_evidence.items():
            # Rebuild snapshot for this domain by applying its evidence
            snapshot = self._rebuild_domain_snapshot(
                session_id,
                domain_name,
                domain_version,
                tuple(ev_list),
                subjects=subjects,
                claims=claims,
            )
            views.append(DomainWorldView(domain=domain_name, snapshot=snapshot))

        # Merge views
        merged = merge_world_views(
            tuple(views),
            session_id=session_id,
            captured_at=utc_now(),
            policy=merge_policy,
        )

        return MergedWorldResult(
            snapshot=merged.snapshot,
            conflicts=merged.conflicts,
            fact_sources=merged.fact_domain_sources,
            identity_mappings=merged.identity_mappings,
        )

    def _rebuild_domain_snapshot(
        self,
        session_id: SessionId,
        domain_name: str,
        domain_version: str,
        evidence: tuple[Evidence, ...],
        *,
        subjects: tuple[str, ...] = (),
        claims: tuple[str, ...] = (),
    ) -> WorldSnapshot:
        """Rebuild a world snapshot for a single domain from its evidence."""
        # Temporarily apply evidence to a fresh model instance
        # Use the base model's internal structure but only with this domain's evidence
        # For simplicity, we directly build facts/entities/relations from evidence
        facts: dict[tuple[str, str], WorldFact] = {}
        entities: dict[EntityId, WorldEntity] = {}
        relations: dict[tuple[EntityId, str, EntityId], WorldRelation] = {}

        # Sort evidence by observed_at for deterministic ordering
        ordered_evidence = tuple(
            sorted(evidence, key=lambda item: (item.observed_at, str(item.id)))
        )

        for ev in ordered_evidence:
            if subjects and ev.subject not in subjects:
                continue
            if claims and ev.claim not in claims:
                continue

            # Apply fact
            key = (ev.subject, ev.claim)
            current_fact = facts.get(key)
            new_fact = WorldFact(
                ev.subject,
                ev.claim,
                ev.value,
                ev.confidence,
                ev.observed_at,
                (ev.id,),
            )
            if current_fact is None:
                facts[key] = new_fact
            else:
                # Choose higher confidence/more recent
                if (ev.confidence, ev.observed_at) > (
                    current_fact.confidence,
                    current_fact.observed_at,
                ):
                    # Merge evidence IDs
                    merged = WorldFact(
                        ev.subject,
                        ev.claim,
                        ev.value,
                        ev.confidence,
                        ev.observed_at,
                        tuple(dict.fromkeys((*current_fact.evidence_ids, ev.id))),
                    )
                    facts[key] = merged

            # Apply entity (if claim is "kind")
            if ev.claim == "kind" and isinstance(ev.value, str) and ev.value.strip():
                entity_id = EntityId(ev.subject)
                existing_entity = entities.get(entity_id)
                new_kind = ev.value.strip()
                if existing_entity is None:
                    entities[entity_id] = WorldEntity(
                        entity_id,
                        new_kind,
                        immutable_json({}),
                        (ev.id,),
                    )
                else:
                    # Update kind (overwrites "unknown" placeholder) and merge evidence IDs
                    merged_entity = WorldEntity(
                        entity_id,
                        new_kind,
                        existing_entity.attributes,
                        tuple(dict.fromkeys((*existing_entity.evidence_ids, ev.id))),
                    )
                    entities[entity_id] = merged_entity

            # Apply entity attributes (any non-kind, non-relation fact)
            if ev.claim != "kind" and not ev.claim.startswith("relation:"):
                entity_id = EntityId(ev.subject)
                existing_entity = entities.get(entity_id)
                if existing_entity is None:
                    # Create entity with just this attribute, kind unknown for now
                    entities[entity_id] = WorldEntity(
                        entity_id,
                        "unknown",
                        immutable_json({ev.claim: ev.value}),
                        (ev.id,),
                    )
                else:
                    # Merge attribute into existing entity
                    merged_attrs = _merge_json_mappings(
                        existing_entity.attributes,
                        immutable_json({ev.claim: ev.value}),
                    )
                    merged_entity = WorldEntity(
                        entity_id,
                        existing_entity.kind,
                        merged_attrs,
                        tuple(dict.fromkeys((*existing_entity.evidence_ids, ev.id))),
                    )
                    entities[entity_id] = merged_entity

            # Apply relations
            if ev.claim.startswith("relation:"):
                relation_name = ev.claim[len("relation:") :].strip()
                if relation_name:
                    targets = _relation_targets(ev.value)
                    for target in targets:
                        rel_key = (EntityId(ev.subject), relation_name, EntityId(target))
                        existing_rel = relations.get(rel_key)
                        new_rel = WorldRelation(
                            EntityId(ev.subject),
                            relation_name,
                            EntityId(target),
                            (ev.id,),
                        )
                        if existing_rel is None:
                            relations[rel_key] = new_rel
                        else:
                            merged_rel = WorldRelation(
                                existing_rel.source,
                                existing_rel.relation,
                                existing_rel.target,
                                tuple(
                                    dict.fromkeys(existing_rel.evidence_ids + new_rel.evidence_ids)
                                ),
                            )
                            relations[rel_key] = merged_rel

        # Build snapshot
        fact_list = tuple(sorted(facts.values(), key=lambda f: (f.subject, f.claim)))
        entity_list = tuple(sorted(entities.values(), key=lambda e: str(e.id)))
        relation_list = tuple(
            sorted(relations.values(), key=lambda r: (str(r.source), r.relation, str(r.target)))
        )

        return WorldSnapshot(
            session_id,
            facts=fact_list,
            entities=entity_list,
            relations=relation_list,
            captured_at=utc_now(),
        )


def _merge_json_mappings(*items: JsonMapping) -> JsonMapping:
    merged: dict[str, JsonValue] = {}
    for item in items:
        merged.update(item)
    return immutable_json(merged)


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
