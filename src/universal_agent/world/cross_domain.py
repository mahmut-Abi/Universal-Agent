from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NewType

from universal_agent.core import (
    JsonMapping,
    JsonValue,
    SessionId,
    dumps_json,
    immutable_json,
    utc_now,
)
from universal_agent.evidence import EvidenceId
from universal_agent.world.models import (
    EntityId,
    WorldEntity,
    WorldFact,
    WorldRelation,
    WorldSnapshot,
)


@dataclass(frozen=True, slots=True)
class DomainWorldView:
    domain: str
    snapshot: WorldSnapshot


@dataclass(frozen=True, slots=True)
class CrossDomainConflict:
    subject: str
    claim: str
    values: tuple[JsonValue, ...]
    domains: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FactDomainSource:
    subject: str
    claim: str
    domains: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EntityIdentityMapping:
    canonical_id: EntityId
    aliases: tuple[EntityId, ...]
    evidence_ids: tuple[EvidenceId, ...] = ()


@dataclass(frozen=True, slots=True)
class _FactEntry:
    domain: str
    subject: str
    fact: WorldFact


class WorldFactMergeStrategy(StrEnum):
    CONFIDENCE_THEN_RECENCY = "confidence_then_recency"
    RECENCY_THEN_CONFIDENCE = "recency_then_confidence"


class WorldConflictResolutionStrategy(StrEnum):
    SELECTED_FACT = "selected_fact"
    REQUIRE_REVIEW = "require_review"
    PREFER_DOMAIN = "prefer_domain"


class WorldConflictResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    REVIEW_REQUIRED = "review_required"


@dataclass(frozen=True, slots=True)
class WorldConflictResolution:
    subject: str
    claim: str
    status: WorldConflictResolutionStatus
    selected_value: JsonValue
    selected_domains: tuple[str, ...]
    selected_evidence_ids: tuple[EvidenceId, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class WorldMergePolicy:
    identity_relation_names: tuple[str, ...] = ("same_as",)
    fact_strategy: WorldFactMergeStrategy = WorldFactMergeStrategy.CONFIDENCE_THEN_RECENCY
    conflict_strategy: WorldConflictResolutionStrategy = (
        WorldConflictResolutionStrategy.SELECTED_FACT
    )
    domain_priority: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MergedWorld:
    snapshot: WorldSnapshot
    conflicts: tuple[CrossDomainConflict, ...]
    fact_domain_sources: tuple[FactDomainSource, ...] = ()
    identity_mappings: tuple[EntityIdentityMapping, ...] = ()
    conflict_resolutions: tuple[WorldConflictResolution, ...] = ()


DomainId = NewType("DomainId", str)


def detect_cross_domain_conflicts(
    views: tuple[DomainWorldView, ...],
    *,
    identity_relation_names: tuple[str, ...] = ("same_as",),
    policy: WorldMergePolicy | None = None,
) -> tuple[CrossDomainConflict, ...]:
    merge_policy = _resolve_merge_policy(policy, identity_relation_names)
    identity_mappings = build_entity_identity_mappings(
        _all_relations(views),
        identity_relation_names=merge_policy.identity_relation_names,
    )
    canonical_ids = _canonical_id_mapping(identity_mappings)
    grouped: dict[tuple[str, str], list[tuple[str, JsonValue]]] = defaultdict(list)
    for view in views:
        for fact in view.snapshot.facts:
            subject = str(_canonical_entity_id(EntityId(fact.subject), canonical_ids))
            grouped[(subject, fact.claim)].append((view.domain, fact.value))

    conflicts: list[CrossDomainConflict] = []
    for (subject, claim), entries in grouped.items():
        distinct: dict[str, JsonValue] = {}
        for _, value in entries:
            distinct.setdefault(dumps_json(value), value)
        if len(distinct) > 1:
            domains = tuple(sorted({domain for domain, _ in entries}))
            conflicts.append(
                CrossDomainConflict(
                    subject,
                    claim,
                    tuple(distinct.values()),
                    domains,
                )
            )
    conflicts.sort(key=lambda item: (item.subject, item.claim))
    return tuple(conflicts)


def merge_world_views(
    views: tuple[DomainWorldView, ...],
    *,
    session_id: SessionId | None = None,
    captured_at: datetime | None = None,
    identity_relation_names: tuple[str, ...] = ("same_as",),
    policy: WorldMergePolicy | None = None,
) -> MergedWorld:
    merge_policy = _resolve_merge_policy(policy, identity_relation_names)
    identity_mappings = build_entity_identity_mappings(
        _all_relations(views),
        identity_relation_names=merge_policy.identity_relation_names,
    )
    canonical_ids = _canonical_id_mapping(identity_mappings)
    identity_relation_set = frozenset(merge_policy.identity_relation_names)
    conflicts = detect_cross_domain_conflicts(
        views,
        policy=merge_policy,
    )

    fact_entries: dict[tuple[str, str], list[_FactEntry]] = defaultdict(list)
    fact_domains: dict[tuple[str, str], list[str]] = defaultdict(list)

    for view in views:
        for fact in view.snapshot.facts:
            subject = str(_canonical_entity_id(EntityId(fact.subject), canonical_ids))
            key = (subject, fact.claim)
            fact_domains[key].append(view.domain)
            fact_entries[key].append(_FactEntry(view.domain, subject, fact))

    merged_facts: dict[tuple[str, str], WorldFact] = {}
    for (subject, claim), entries in fact_entries.items():
        chosen = _choose_fact_entry(tuple(entries), merge_policy)
        evidence_ids = tuple(
            dict.fromkeys(
                evidence_id for entry in entries for evidence_id in entry.fact.evidence_ids
            )
        )
        merged_facts[(subject, claim)] = WorldFact(
            subject,
            claim,
            chosen.fact.value,
            chosen.fact.confidence,
            chosen.fact.observed_at,
            evidence_ids,
        )

    merged_entities: dict[EntityId, WorldEntity] = {}
    for view in views:
        for entity in view.snapshot.entities:
            entity_id = _canonical_entity_id(entity.id, canonical_ids)
            existing_entity = merged_entities.get(entity_id)
            if existing_entity is None:
                merged_entities[entity_id] = WorldEntity(
                    entity_id,
                    entity.kind,
                    entity.attributes,
                    entity.evidence_ids,
                )
                continue
            attributes: JsonMapping = immutable_json(
                dict(existing_entity.attributes) | dict(entity.attributes)
            )
            merged_entities[entity_id] = WorldEntity(
                entity_id,
                _merge_entity_kind(existing_entity.kind, entity.kind),
                attributes,
                tuple(dict.fromkeys(existing_entity.evidence_ids + entity.evidence_ids)),
            )

    merged_relations: dict[tuple[EntityId, str, EntityId], WorldRelation] = {}
    for view in views:
        for relation in view.snapshot.relations:
            if relation.relation in identity_relation_set:
                continue
            source = _canonical_entity_id(relation.source, canonical_ids)
            target = _canonical_entity_id(relation.target, canonical_ids)
            if source == target:
                continue
            relation_key = (source, relation.relation, target)
            existing_relation = merged_relations.get(relation_key)
            if existing_relation is None:
                merged_relations[relation_key] = WorldRelation(
                    source,
                    relation.relation,
                    target,
                    relation.evidence_ids,
                )
                continue
            merged_relations[relation_key] = WorldRelation(
                source,
                relation.relation,
                target,
                tuple(dict.fromkeys(existing_relation.evidence_ids + relation.evidence_ids)),
            )

    fact_sources = tuple(
        FactDomainSource(subject, claim, tuple(sorted(set(domains))))
        for (subject, claim), domains in fact_domains.items()
    )
    conflict_resolutions = resolve_cross_domain_conflicts(
        conflicts,
        fact_entries,
        policy=merge_policy,
    )

    merged_snapshot = WorldSnapshot(
        session_id if session_id is not None else SessionId("cross-domain-merged"),
        facts=tuple(sorted(merged_facts.values(), key=lambda item: (item.subject, item.claim))),
        entities=tuple(sorted(merged_entities.values(), key=lambda item: str(item.id))),
        relations=tuple(
            sorted(
                merged_relations.values(),
                key=lambda item: (str(item.source), item.relation, str(item.target)),
            )
        ),
        captured_at=captured_at if captured_at is not None else utc_now(),
    )

    return MergedWorld(
        merged_snapshot,
        conflicts,
        fact_sources,
        identity_mappings,
        conflict_resolutions,
    )


def resolve_cross_domain_conflicts(
    conflicts: tuple[CrossDomainConflict, ...],
    fact_entries: Mapping[tuple[str, str], Sequence[_FactEntry]],
    *,
    policy: WorldMergePolicy,
) -> tuple[WorldConflictResolution, ...]:
    """Resolve detected cross-domain fact conflicts deterministically.

    Strategies:

    - ``selected_fact``: the value chosen by the configured fact merge
      strategy wins and the conflict is marked resolved.
    - ``prefer_domain``: the highest-priority domain that observed the claim
      wins; when none of the preferred domains observed the claim, the
      conflict is escalated for review instead of silently ignoring the
      configured preference.
    - ``require_review``: every conflict is escalated for review while the
      fact-strategy choice is still reported for observability.
    """
    resolutions: list[WorldConflictResolution] = []
    for conflict in conflicts:
        entries = tuple(fact_entries.get((conflict.subject, conflict.claim), ()))
        chosen = _choose_fact_entry(entries, policy) if entries else None
        selected_value = chosen.fact.value if chosen is not None else None
        selected_domains: tuple[str, ...] = (
            (chosen.domain,) if chosen is not None else conflict.domains
        )
        selected_evidence_ids = chosen.fact.evidence_ids if chosen is not None else ()
        if chosen is None:
            status = WorldConflictResolutionStatus.REVIEW_REQUIRED
            reason = "no fact entries available for conflicting claim"
        elif policy.conflict_strategy is WorldConflictResolutionStrategy.PREFER_DOMAIN:
            preferred = next(
                (
                    entry
                    for domain in policy.domain_priority
                    for entry in entries
                    if entry.domain == domain
                ),
                None,
            )
            if preferred is None:
                status = WorldConflictResolutionStatus.REVIEW_REQUIRED
                reason = (
                    "no preferred domain "
                    f"{policy.domain_priority} observed claim '{conflict.claim}'"
                )
            else:
                status = WorldConflictResolutionStatus.RESOLVED
                selected_value = preferred.fact.value
                selected_domains = (preferred.domain,)
                selected_evidence_ids = preferred.fact.evidence_ids
                reason = f"preferred domain '{preferred.domain}'"
        elif policy.conflict_strategy is WorldConflictResolutionStrategy.REQUIRE_REVIEW:
            status = WorldConflictResolutionStatus.REVIEW_REQUIRED
            reason = "conflict policy requires review"
        else:
            status = WorldConflictResolutionStatus.RESOLVED
            reason = f"selected fact via {policy.fact_strategy.value} merge strategy"
        resolutions.append(
            WorldConflictResolution(
                conflict.subject,
                conflict.claim,
                status,
                selected_value,
                selected_domains,
                selected_evidence_ids,
                reason,
            )
        )
    return tuple(resolutions)


def build_entity_identity_mappings(
    relations: tuple[WorldRelation, ...],
    *,
    identity_relation_names: tuple[str, ...] = ("same_as",),
) -> tuple[EntityIdentityMapping, ...]:
    identity_relation_set = frozenset(identity_relation_names)
    parent: dict[EntityId, EntityId] = {}

    def find(entity_id: EntityId) -> EntityId:
        parent.setdefault(entity_id, entity_id)
        current = entity_id
        while parent[current] != current:
            current = parent[current]
        root = current
        current = entity_id
        while parent[current] != current:
            next_id = parent[current]
            parent[current] = root
            current = next_id
        return root

    def union(left: EntityId, right: EntityId) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        canonical = min((left_root, right_root), key=str)
        other = right_root if canonical == left_root else left_root
        parent[other] = canonical

    identity_relations = tuple(
        relation for relation in relations if relation.relation in identity_relation_set
    )
    for relation in identity_relations:
        union(relation.source, relation.target)

    grouped: dict[EntityId, list[EntityId]] = defaultdict(list)
    for entity_id in parent:
        grouped[find(entity_id)].append(entity_id)

    mappings: list[EntityIdentityMapping] = []
    for canonical_id, aliases in grouped.items():
        deduped_aliases = tuple(sorted(set(aliases), key=str))
        if len(deduped_aliases) < 2:
            continue
        evidence_ids = tuple(
            dict.fromkeys(
                evidence_id
                for relation in identity_relations
                if find(relation.source) == canonical_id
                for evidence_id in relation.evidence_ids
            )
        )
        mappings.append(EntityIdentityMapping(canonical_id, deduped_aliases, evidence_ids))
    return tuple(sorted(mappings, key=lambda item: str(item.canonical_id)))


def _all_relations(views: tuple[DomainWorldView, ...]) -> tuple[WorldRelation, ...]:
    return tuple(relation for view in views for relation in view.snapshot.relations)


def _canonical_id_mapping(
    mappings: tuple[EntityIdentityMapping, ...],
) -> Mapping[EntityId, EntityId]:
    return {alias: mapping.canonical_id for mapping in mappings for alias in mapping.aliases}


def _canonical_entity_id(
    entity_id: EntityId,
    canonical_ids: Mapping[EntityId, EntityId],
) -> EntityId:
    return canonical_ids.get(entity_id, entity_id)


def _merge_entity_kind(existing: str, incoming: str) -> str:
    if existing == "unknown" and incoming != "unknown":
        return incoming
    return existing or incoming


def _resolve_merge_policy(
    policy: WorldMergePolicy | None,
    identity_relation_names: tuple[str, ...],
) -> WorldMergePolicy:
    if policy is not None:
        return policy
    return WorldMergePolicy(identity_relation_names=identity_relation_names)


def _choose_fact_entry(
    entries: tuple[_FactEntry, ...],
    policy: WorldMergePolicy,
) -> _FactEntry:
    """Pick the winning fact entry for a ``(subject, claim)`` key.

    Folds the pairwise comparison from :func:`_choose_fact` across all
    entries in stable view order, so ties deterministically keep the
    earlier view's fact.
    """
    if not entries:
        raise ValueError("cannot choose a fact entry from an empty sequence")
    chosen = entries[0]
    for entry in entries[1:]:
        if _choose_fact(chosen.fact, entry.fact, policy.fact_strategy) is entry.fact:
            chosen = entry
    return chosen


def _choose_fact(
    existing: WorldFact,
    incoming: WorldFact,
    strategy: WorldFactMergeStrategy,
) -> WorldFact:
    if strategy is WorldFactMergeStrategy.RECENCY_THEN_CONFIDENCE:
        if (incoming.observed_at, incoming.confidence) > (
            existing.observed_at,
            existing.confidence,
        ):
            return incoming
        return existing
    if (incoming.confidence, incoming.observed_at) > (
        existing.confidence,
        existing.observed_at,
    ):
        return incoming
    return existing
