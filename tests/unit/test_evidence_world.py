from datetime import UTC, datetime, timedelta

from universal_agent.core import (
    JsonValue,
    Observation,
    ObservationStatus,
    SessionId,
    Task,
    immutable_json,
    new_action_id,
    new_observation_id,
)
from universal_agent.evidence import Evidence, EvidenceQuery, InMemoryEvidenceStore
from universal_agent.world import EntityId, FactWorldUpdater, InMemoryWorldModel


def make_evidence(
    *,
    value: JsonValue,
    confidence: float,
    seconds: int,
    claim: str = "healthy",
    subject: str = "deployment/example",
) -> Evidence:
    task = Task("Inspect", ("healthy",))
    action_id = new_action_id()
    observation = Observation(
        new_observation_id(),
        action_id,
        task.id,
        "test",
        ObservationStatus.SUCCEEDED,
        immutable_json({claim: value}),
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds),
    )
    return Evidence(
        SessionId("session-test"),
        task.id,
        action_id,
        observation.id,
        subject,
        claim,
        value,
        observation.source,
        confidence,
        observed_at=observation.observed_at,
    )


def test_evidence_store_is_idempotent_and_queryable() -> None:
    store = InMemoryEvidenceStore()
    evidence = make_evidence(value=True, confidence=0.9, seconds=1)

    assert store.add(evidence)
    assert not store.add(evidence)
    assert store.query(EvidenceQuery(evidence.session_id, claim="healthy")) == (evidence,)
    assert store.query(EvidenceQuery(evidence.session_id, subject="missing")) == ()


def test_world_model_preserves_conflicting_provenance() -> None:
    model = InMemoryWorldModel()
    older_high_confidence = make_evidence(value=False, confidence=0.99, seconds=1)
    newer_low_confidence = make_evidence(value=True, confidence=0.7, seconds=2)

    model.apply_fact(older_high_confidence)
    model.apply_fact(newer_low_confidence)
    snapshot = model.snapshot(SessionId("session-test"))

    assert len(snapshot.facts) == 1
    assert snapshot.facts[0].value is False
    assert snapshot.facts[0].evidence_ids == (
        older_high_confidence.id,
        newer_low_confidence.id,
    )
    history = snapshot.fact_history_for("deployment/example", "healthy")
    assert history is not None
    assert history.conflicting is True
    assert history.current.value is False
    assert [item.value for item in history.candidates] == [False, True]
    assert [item.evidence_id for item in history.candidates] == [
        older_high_confidence.id,
        newer_low_confidence.id,
    ]
    assert snapshot.conflicting_facts() == (history,)


def test_fact_world_updater_projects_entities_from_kind_evidence() -> None:
    model = InMemoryWorldModel()
    updater = FactWorldUpdater()
    healthy = make_evidence(value=True, confidence=0.9, seconds=1)
    kind = make_evidence(value="Deployment", confidence=0.9, seconds=2, claim="kind")

    assert updater.apply(model, healthy)
    assert updater.apply(model, kind)
    snapshot = model.snapshot(SessionId("session-test"))

    entity = snapshot.entity_for(EntityId("deployment/example"))
    assert entity is not None
    assert entity.kind == "Deployment"
    assert entity.attributes["healthy"] is True
    assert entity.evidence_ids == (kind.id,)


def test_entity_kind_uses_current_fact_when_kind_evidence_conflicts() -> None:
    model = InMemoryWorldModel()
    updater = FactWorldUpdater()
    low_confidence_kind = make_evidence(
        value="ReplicaSet",
        confidence=0.4,
        seconds=1,
        claim="kind",
    )
    high_confidence_kind = make_evidence(
        value="Deployment",
        confidence=0.9,
        seconds=2,
        claim="kind",
    )

    assert updater.apply(model, high_confidence_kind)
    assert updater.apply(model, low_confidence_kind)
    snapshot = model.snapshot(SessionId("session-test"))

    entity = snapshot.entity_for("deployment/example")
    assert entity is not None
    assert entity.kind == "Deployment"
    assert entity.evidence_ids == (high_confidence_kind.id, low_confidence_kind.id)


def test_fact_world_updater_projects_relations_from_relation_evidence() -> None:
    model = InMemoryWorldModel()
    updater = FactWorldUpdater()
    relation = make_evidence(
        value=["pod/example-1", "pod/example-2"],
        confidence=0.9,
        seconds=1,
        claim="relation:owns",
    )

    assert updater.apply(model, relation)
    assert not updater.apply(model, relation)
    snapshot = model.snapshot(SessionId("session-test"))

    assert [item.target for item in snapshot.relations_for(source="deployment/example")] == [
        EntityId("pod/example-1"),
        EntityId("pod/example-2"),
    ]
    assert snapshot.relations_for(relation="owns")[0].evidence_ids == (relation.id,)


def test_world_snapshot_queries_entity_neighborhoods() -> None:
    model = InMemoryWorldModel()
    updater = FactWorldUpdater()
    for item in (
        make_evidence(value=True, confidence=0.9, seconds=1),
        make_evidence(value="Deployment", confidence=0.9, seconds=2, claim="kind"),
        make_evidence(
            value="pod/example-1",
            confidence=0.9,
            seconds=3,
            claim="relation:owns",
        ),
        make_evidence(
            value="Pod",
            confidence=0.9,
            seconds=4,
            claim="kind",
            subject="pod/example-1",
        ),
    ):
        updater.apply(model, item)

    snapshot = model.snapshot(SessionId("session-test"))
    deployment = snapshot.neighborhood_for("deployment/example")
    pod = snapshot.neighborhood_for("pod/example-1")

    assert deployment.root is not None
    assert deployment.root.kind == "Deployment"
    assert [fact.claim for fact in deployment.facts] == ["healthy", "kind", "relation:owns"]
    assert [relation.target for relation in deployment.outgoing_relations] == [
        EntityId("pod/example-1")
    ]
    assert deployment.incoming_relations == ()
    assert [entity.kind for entity in deployment.related_entities] == ["Pod"]
    assert pod.root is not None
    assert pod.root.kind == "Pod"
    assert [relation.source for relation in pod.incoming_relations] == [
        EntityId("deployment/example")
    ]
    assert pod.outgoing_relations == ()
