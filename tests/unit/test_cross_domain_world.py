from __future__ import annotations

from datetime import UTC, datetime

from universal_agent.core import (
    ActionId,
    JsonValue,
    ObservationId,
    SessionId,
    TaskId,
)
from universal_agent.evidence import Evidence, EvidenceId
from universal_agent.world.cross_domain import (
    CrossDomainConflict,
    DomainWorldView,
    EntityIdentityMapping,
    FactDomainSource,
    MergedWorld,
    WorldFactMergeStrategy,
    WorldMergePolicy,
    build_entity_identity_mappings,
    detect_cross_domain_conflicts,
    merge_world_views,
)
from universal_agent.world.model import FactWorldUpdater, InMemoryWorldModel
from universal_agent.world.models import EntityId, WorldFact, WorldRelation, WorldSnapshot

K8S_SESSION = SessionId("k8s-session")
DB_SESSION = SessionId("db-session")


def _evidence(
    session_id: SessionId,
    subject: str,
    claim: str,
    value: JsonValue,
    source: str,
    *,
    confidence: float = 1.0,
    evidence_id: str = "ev-1",
) -> Evidence:
    return Evidence(
        session_id,
        TaskId("task-1"),
        ActionId("action-1"),
        ObservationId("obs-1"),
        subject,
        claim,
        value,
        source,
        confidence=confidence,
        id=EvidenceId(evidence_id),
    )


def _k8s_view() -> DomainWorldView:
    model = InMemoryWorldModel()
    updater = FactWorldUpdater()
    updater.apply(model, _evidence(K8S_SESSION, "deployment/dify-api", "kind", "Deployment", "k8s"))
    updater.apply(model, _evidence(K8S_SESSION, "deployment/dify-api", "replicas", 3, "k8s"))
    updater.apply(model, _evidence(K8S_SESSION, "deployment/dify-api", "available", 2, "k8s"))
    updater.apply(model, _evidence(K8S_SESSION, "deployment/dify-api", "status", "degraded", "k8s"))
    return DomainWorldView("kubernetes", model.snapshot(K8S_SESSION))


def _db_view() -> DomainWorldView:
    model = InMemoryWorldModel()
    updater = FactWorldUpdater()
    updater.apply(model, _evidence(DB_SESSION, "deployment/dify-api", "kind", "Deployment", "db"))
    updater.apply(model, _evidence(DB_SESSION, "deployment/dify-api", "replicas", 3, "db"))
    updater.apply(model, _evidence(DB_SESSION, "deployment/dify-api", "status", "healthy", "db"))
    updater.apply(model, _evidence(DB_SESSION, "database/pg", "kind", "PostgreSQL", "db"))
    return DomainWorldView("database", model.snapshot(DB_SESSION))


def _empty_view(domain: str) -> DomainWorldView:
    return DomainWorldView(domain, WorldSnapshot(SessionId(f"{domain}-empty")))


def test_consistent_facts_merge_with_multi_domain_source() -> None:
    merged = merge_world_views((_k8s_view(), _db_view()))

    replicas = merged.snapshot.value_for("replicas", subject="deployment/dify-api")
    assert replicas == 3

    sources = {item.subject: item for item in merged.fact_domain_sources}
    assert set(sources["deployment/dify-api"].domains) == {"kubernetes", "database"}


def test_conflicting_facts_detected() -> None:
    merged = merge_world_views((_k8s_view(), _db_view()))

    conflicts = detect_cross_domain_conflicts((_k8s_view(), _db_view()))
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.subject == "deployment/dify-api"
    assert conflict.claim == "status"
    assert set(conflict.values) == {"degraded", "healthy"}
    assert set(conflict.domains) == {"kubernetes", "database"}
    assert merged.conflicts == conflicts


def test_merged_snapshot_queryable() -> None:
    merged = merge_world_views((_k8s_view(), _db_view()))

    assert merged.snapshot.entity_for(EntityId("deployment/dify-api")) is not None
    assert merged.snapshot.entity_for(EntityId("database/pg")) is not None

    status = merged.snapshot.value_for("status", subject="deployment/dify-api")
    assert status in {"degraded", "healthy"}

    assert merged.snapshot.value_for("kind", subject="database/pg") == "PostgreSQL"


def test_domain_source_annotation_present() -> None:
    merged = merge_world_views((_k8s_view(), _db_view()))

    sources = {item.subject: item for item in merged.fact_domain_sources}
    assert sources["deployment/dify-api"].domains == ("database", "kubernetes")
    assert sources["database/pg"].domains == ("database",)


def test_identity_relation_canonicalizes_cross_domain_facts_entities_and_relations() -> None:
    canonical_id = "deployment/dify-api"
    alias_id = "k8s://prod/default/deployment/dify-api"

    k8s = InMemoryWorldModel()
    observability = InMemoryWorldModel()
    updater = FactWorldUpdater()
    updater.apply(k8s, _evidence(K8S_SESSION, canonical_id, "kind", "Deployment", "k8s"))
    updater.apply(k8s, _evidence(K8S_SESSION, canonical_id, "available", 2, "k8s"))
    updater.apply(
        observability,
        _evidence(DB_SESSION, alias_id, "kind", "Deployment", "prometheus", evidence_id="ev-2"),
    )
    updater.apply(
        observability,
        _evidence(DB_SESSION, alias_id, "error_rate", 0.08, "prometheus", evidence_id="ev-3"),
    )
    updater.apply(
        observability,
        _evidence(
            DB_SESSION,
            alias_id,
            "relation:same_as",
            canonical_id,
            "prometheus",
            evidence_id="ev-4",
        ),
    )
    updater.apply(
        observability,
        _evidence(
            DB_SESSION,
            alias_id,
            "relation:emits",
            "metric/http_5xx_rate",
            "prometheus",
            evidence_id="ev-5",
        ),
    )
    updater.apply(
        observability,
        _evidence(
            DB_SESSION,
            "metric/http_5xx_rate",
            "kind",
            "Metric",
            "prometheus",
            evidence_id="ev-6",
        ),
    )

    merged = merge_world_views(
        (
            DomainWorldView("kubernetes", k8s.snapshot(K8S_SESSION)),
            DomainWorldView("observability", observability.snapshot(DB_SESSION)),
        )
    )

    mapping = merged.identity_mappings[0]
    assert isinstance(mapping, EntityIdentityMapping)
    assert mapping.canonical_id == EntityId(canonical_id)
    assert mapping.aliases == (
        EntityId(canonical_id),
        EntityId(alias_id),
    )
    assert mapping.evidence_ids == (EvidenceId("ev-4"),)
    assert merged.snapshot.entity_for(canonical_id) is not None
    assert merged.snapshot.entity_for(alias_id) is None
    assert merged.snapshot.value_for("error_rate", subject=canonical_id) == 0.08
    assert merged.snapshot.value_for("available", subject=canonical_id) == 2
    assert merged.snapshot.relations_for(relation="same_as") == ()
    assert (
        merged.snapshot.relations_for(
            source=canonical_id,
            relation="emits",
            target="metric/http_5xx_rate",
        )
        != ()
    )


def test_identity_relation_reports_conflict_against_canonical_subject() -> None:
    canonical_id = "deployment/dify-api"
    alias_id = "k8s://prod/default/deployment/dify-api"

    k8s = InMemoryWorldModel()
    observability = InMemoryWorldModel()
    updater = FactWorldUpdater()
    updater.apply(k8s, _evidence(K8S_SESSION, canonical_id, "status", "degraded", "k8s"))
    updater.apply(
        observability,
        _evidence(DB_SESSION, alias_id, "status", "healthy", "prometheus", evidence_id="ev-2"),
    )
    updater.apply(
        observability,
        _evidence(
            DB_SESSION,
            alias_id,
            "relation:same_as",
            canonical_id,
            "prometheus",
            evidence_id="ev-3",
        ),
    )
    views = (
        DomainWorldView("kubernetes", k8s.snapshot(K8S_SESSION)),
        DomainWorldView("observability", observability.snapshot(DB_SESSION)),
    )

    conflicts = detect_cross_domain_conflicts(views)
    merged = merge_world_views(views)

    assert conflicts == merged.conflicts
    assert len(conflicts) == 1
    assert conflicts[0].subject == canonical_id
    assert set(conflicts[0].values) == {"degraded", "healthy"}


def test_identity_mapping_builder_collapses_transitive_aliases() -> None:
    mappings = build_entity_identity_mappings(
        (
            WorldRelation(EntityId("b"), "same_as", EntityId("c"), (EvidenceId("ev-1"),)),
            WorldRelation(EntityId("a"), "same_as", EntityId("b"), (EvidenceId("ev-2"),)),
        )
    )

    assert mappings == (
        EntityIdentityMapping(
            EntityId("a"),
            (EntityId("a"), EntityId("b"), EntityId("c")),
            (EvidenceId("ev-1"), EvidenceId("ev-2")),
        ),
    )


def test_world_merge_policy_can_prefer_recency_over_confidence() -> None:
    older_high_confidence = WorldFact(
        "deployment/dify-api",
        "status",
        "degraded",
        0.99,
        datetime(2026, 1, 1, tzinfo=UTC),
        (EvidenceId("ev-old"),),
    )
    newer_low_confidence = WorldFact(
        "deployment/dify-api",
        "status",
        "healthy",
        0.5,
        datetime(2026, 1, 2, tzinfo=UTC),
        (EvidenceId("ev-new"),),
    )
    views = (
        DomainWorldView("kubernetes", WorldSnapshot(K8S_SESSION, facts=(older_high_confidence,))),
        DomainWorldView("observability", WorldSnapshot(DB_SESSION, facts=(newer_low_confidence,))),
    )

    default_merged = merge_world_views(views)
    recency_merged = merge_world_views(
        views,
        policy=WorldMergePolicy(
            fact_strategy=WorldFactMergeStrategy.RECENCY_THEN_CONFIDENCE,
        ),
    )

    assert default_merged.snapshot.value_for("status", subject="deployment/dify-api") == "degraded"
    assert recency_merged.snapshot.value_for("status", subject="deployment/dify-api") == "healthy"


def test_empty_views_merge_cleanly() -> None:
    merged = merge_world_views((_empty_view("a"), _empty_view("b")))

    assert isinstance(merged, MergedWorld)
    assert merged.conflicts == ()
    assert merged.snapshot.facts == ()
    assert merged.fact_domain_sources == ()
    assert merged.identity_mappings == ()


def test_conflict_is_typed() -> None:
    conflict = CrossDomainConflict("s", "c", ("x", "y"), ("d1", "d2"))
    assert isinstance(conflict, CrossDomainConflict)
    assert isinstance(FactDomainSource("s", "c", ("d1",)), FactDomainSource)
