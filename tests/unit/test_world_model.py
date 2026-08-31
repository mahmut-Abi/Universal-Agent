from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

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
from universal_agent.evidence import Evidence, EvidenceId
from universal_agent.world import (
    EntityId,
    FactWorldUpdater,
    InMemoryWorldModel,
    WorldEntity,
    WorldFact,
    WorldFactEvidence,
    WorldFactHistory,
    WorldGraph,
    WorldGraphNode,
    WorldGraphQuery,
    WorldNeighborhood,
    WorldRelation,
    WorldRelationDirection,
    WorldSnapshot,
)

SESSION_TEST_ID = SessionId("session-test")


def make_evidence(
    *,
    value: JsonValue,
    confidence: float,
    seconds: int,
    claim: str = "healthy",
    subject: str = "deployment/example",
    session: SessionId = SESSION_TEST_ID,
) -> Evidence:
    task = Task("Inspect", ("healthy",))
    action_id = new_action_id()
    observed_at = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds)
    observation = Observation(
        new_observation_id(),
        action_id,
        task.id,
        "test",
        ObservationStatus.SUCCEEDED,
        immutable_json({claim: value}),
        observed_at,
    )
    return Evidence(
        session,
        task.id,
        action_id,
        observation.id,
        subject,
        claim,
        value,
        observation.source,
        confidence,
        observed_at=observed_at,
    )


@pytest.mark.unit
def test_value_for_returns_current_value_and_filters_by_subject() -> None:
    snapshot = WorldSnapshot(
        SessionId("s"),
        facts=(
            WorldFact("pod/a", "ready", True, 0.9, datetime(2026, 1, 1, tzinfo=UTC), ()),
            WorldFact("pod/b", "ready", False, 0.9, datetime(2026, 1, 1, tzinfo=UTC), ()),
        ),
    )
    assert snapshot.value_for("ready", subject="pod/a") is True
    assert snapshot.value_for("ready", subject="pod/b") is False
    assert snapshot.value_for("ready") is True
    assert snapshot.value_for("missing") is None
    assert snapshot.value_for("ready", subject="pod/missing") is None


@pytest.mark.unit
def test_entity_for_matches_by_id_or_string() -> None:
    entity = WorldEntity(EntityId("pod/a"), "Pod")
    snapshot = WorldSnapshot(SessionId("s"), entities=(entity,))
    assert snapshot.entity_for("pod/a") is entity
    assert snapshot.entity_for(EntityId("pod/a")) is entity
    assert snapshot.entity_for("pod/missing") is None


@pytest.mark.unit
def test_facts_for_filters_by_subject_and_claims() -> None:
    snapshot = WorldSnapshot(
        SessionId("s"),
        facts=(
            WorldFact("pod/a", "ready", True, 0.9, datetime(2026, 1, 1, tzinfo=UTC), ()),
            WorldFact("pod/a", "restarts", 3, 0.9, datetime(2026, 1, 1, tzinfo=UTC), ()),
            WorldFact("pod/b", "ready", False, 0.9, datetime(2026, 1, 1, tzinfo=UTC), ()),
        ),
    )
    assert len(snapshot.facts_for("pod/a")) == 2
    assert {fact.claim for fact in snapshot.facts_for("pod/a", claims=("ready",))} == {"ready"}
    assert snapshot.facts_for("pod/missing") == ()


@pytest.mark.unit
def test_fact_history_for_returns_matching_history() -> None:
    history = WorldFactHistory(
        "pod/a",
        "ready",
        WorldFact("pod/a", "ready", True, 0.9, datetime(2026, 1, 1, tzinfo=UTC), ()),
        (),
        False,
    )
    snapshot = WorldSnapshot(SessionId("s"), fact_histories=(history,))
    assert snapshot.fact_history_for("pod/a", "ready") is history
    assert snapshot.fact_history_for("pod/a", "missing") is None


@pytest.mark.unit
def test_conflicting_facts_filters_by_subject_and_claim() -> None:
    conflicting = WorldFactHistory(
        "pod/a",
        "ready",
        WorldFact("pod/a", "ready", True, 0.9, datetime(2026, 1, 1, tzinfo=UTC), ()),
        (),
        True,
    )
    other = WorldFactHistory(
        "pod/b",
        "ready",
        WorldFact("pod/b", "ready", False, 0.9, datetime(2026, 1, 1, tzinfo=UTC), ()),
        (),
        True,
    )
    clean = WorldFactHistory(
        "pod/a",
        "restarts",
        WorldFact("pod/a", "restarts", 0, 0.9, datetime(2026, 1, 1, tzinfo=UTC), ()),
        (),
        False,
    )
    snapshot = WorldSnapshot(SessionId("s"), fact_histories=(conflicting, other, clean))
    assert snapshot.conflicting_facts() == (conflicting, other)
    assert snapshot.conflicting_facts(subject="pod/a") == (conflicting,)
    assert snapshot.conflicting_facts(claim="ready") == (conflicting, other)
    assert snapshot.conflicting_facts(subject="pod/a", claim="ready") == (conflicting,)


@pytest.mark.unit
def test_relations_for_filters_by_source_relation_target() -> None:
    relation = WorldRelation(EntityId("pod/a"), "owns", EntityId("pod/b"))
    snapshot = WorldSnapshot(SessionId("s"), relations=(relation,))
    assert snapshot.relations_for(source="pod/a") == (relation,)
    assert snapshot.relations_for(relation="owns") == (relation,)
    assert snapshot.relations_for(target="pod/b") == (relation,)
    assert snapshot.relations_for(source="pod/missing") == ()
    assert snapshot.relations_for(relation="missing") == ()


@pytest.mark.unit
def test_neighborhood_for_collects_outgoing_incoming_related() -> None:
    root = WorldEntity(EntityId("pod/a"), "Pod")
    related = WorldEntity(EntityId("pod/b"), "Pod")
    outgoing = WorldRelation(EntityId("pod/a"), "owns", EntityId("pod/b"))
    incoming = WorldRelation(EntityId("pod/c"), "owns", EntityId("pod/a"))
    fact = WorldFact("pod/a", "ready", True, 0.9, datetime(2026, 1, 1, tzinfo=UTC), ())
    snapshot = WorldSnapshot(
        SessionId("s"),
        facts=(fact,),
        entities=(root, related),
        relations=(outgoing, incoming),
    )
    neighborhood = snapshot.neighborhood_for("pod/a")

    assert isinstance(neighborhood, WorldNeighborhood)
    assert neighborhood.root is root
    assert neighborhood.facts == (fact,)
    assert neighborhood.outgoing_relations == (outgoing,)
    assert neighborhood.incoming_relations == (incoming,)
    assert neighborhood.related_entities == (related,)
    assert snapshot.neighborhood_for("pod/missing").root is None


@pytest.mark.unit
def test_relation_graph_for_traverses_multi_hop_relations() -> None:
    deployment = WorldEntity(EntityId("deployment/api"), "Deployment")
    replica_set = WorldEntity(EntityId("replicaset/api-123"), "ReplicaSet")
    pod = WorldEntity(EntityId("pod/api-123-a"), "Pod")
    metric = WorldEntity(EntityId("metric/http_5xx_rate"), "Metric")
    owns_rs = WorldRelation(EntityId("deployment/api"), "owns", EntityId("replicaset/api-123"))
    owns_pod = WorldRelation(EntityId("replicaset/api-123"), "owns", EntityId("pod/api-123-a"))
    emits_metric = WorldRelation(
        EntityId("pod/api-123-a"),
        "emits",
        EntityId("metric/http_5xx_rate"),
    )
    snapshot = WorldSnapshot(
        SessionId("s"),
        entities=(deployment, replica_set, pod, metric),
        relations=(owns_rs, owns_pod, emits_metric),
    )

    graph = snapshot.relation_graph_for("deployment/api", max_depth=2, relations=("owns",))

    assert isinstance(graph, WorldGraph)
    assert graph.root is deployment
    assert [node.entity_id for node in graph.nodes] == [
        EntityId("deployment/api"),
        EntityId("replicaset/api-123"),
        EntityId("pod/api-123-a"),
    ]
    assert [node.depth for node in graph.nodes] == [0, 1, 2]
    assert graph.relations == (owns_rs, owns_pod)
    assert graph.entities == (deployment, replica_set, pod)


@pytest.mark.unit
def test_relation_graph_for_supports_incoming_and_both_directions() -> None:
    deployment = WorldEntity(EntityId("deployment/api"), "Deployment")
    replica_set = WorldEntity(EntityId("replicaset/api-123"), "ReplicaSet")
    pod = WorldEntity(EntityId("pod/api-123-a"), "Pod")
    owns_rs = WorldRelation(EntityId("deployment/api"), "owns", EntityId("replicaset/api-123"))
    owns_pod = WorldRelation(EntityId("replicaset/api-123"), "owns", EntityId("pod/api-123-a"))
    snapshot = WorldSnapshot(
        SessionId("s"),
        entities=(deployment, replica_set, pod),
        relations=(owns_rs, owns_pod),
    )

    incoming = snapshot.relation_graph_for(
        "pod/api-123-a",
        max_depth=2,
        direction=WorldRelationDirection.INCOMING,
    )
    both = snapshot.relation_graph_for("replicaset/api-123", max_depth=1)

    assert [node.entity_id for node in incoming.nodes] == [
        EntityId("pod/api-123-a"),
        EntityId("replicaset/api-123"),
        EntityId("deployment/api"),
    ]
    assert set(both.relations) == {owns_rs, owns_pod}


@pytest.mark.unit
def test_relation_graph_for_applies_node_predicates() -> None:
    deployment = WorldEntity(EntityId("deployment/api"), "Deployment")
    ready_pod = WorldEntity(EntityId("pod/api-ready"), "Pod")
    failing_pod = WorldEntity(EntityId("pod/api-failing"), "Pod")
    metric = WorldEntity(EntityId("metric/http_5xx_rate"), "Metric")
    ready_relation = WorldRelation(EntityId("deployment/api"), "owns", EntityId("pod/api-ready"))
    failing_relation = WorldRelation(
        EntityId("deployment/api"), "owns", EntityId("pod/api-failing")
    )
    metric_relation = WorldRelation(
        EntityId("deployment/api"),
        "emits",
        EntityId("metric/http_5xx_rate"),
    )
    ready_fact = WorldFact(
        "pod/api-ready",
        "ready",
        True,
        1.0,
        datetime(2026, 1, 1, tzinfo=UTC),
        (),
    )
    failing_fact = WorldFact(
        "pod/api-failing",
        "ready",
        False,
        1.0,
        datetime(2026, 1, 1, tzinfo=UTC),
        (),
    )
    snapshot = WorldSnapshot(
        SessionId("s"),
        facts=(ready_fact, failing_fact),
        entities=(deployment, ready_pod, failing_pod, metric),
        relations=(ready_relation, failing_relation, metric_relation),
    )

    graph = snapshot.relation_graph_for(
        "deployment/api",
        query=WorldGraphQuery(
            max_depth=1,
            relations=("owns", "emits"),
            entity_kinds=("Pod",),
            required_facts=immutable_json({"ready": False}),
        ),
    )

    assert [node.entity_id for node in graph.nodes] == [
        EntityId("deployment/api"),
        EntityId("pod/api-failing"),
    ]
    assert graph.relations == (failing_relation,)
    assert graph.entities == (deployment, failing_pod)


@pytest.mark.unit
def test_relation_graph_for_validates_query() -> None:
    snapshot = WorldSnapshot(
        SessionId("s"),
        entities=(WorldEntity(EntityId("pod/a"), "Pod"),),
    )

    assert isinstance(snapshot.relation_graph_for("pod/a").nodes[0], WorldGraphNode)
    assert snapshot.relation_graph_for("pod/missing").root is None
    with pytest.raises(ValueError, match="max_depth"):
        snapshot.relation_graph_for("pod/a", max_depth=-1)
    with pytest.raises(ValueError, match="direction"):
        snapshot.relation_graph_for("pod/a", direction="sideways")


@pytest.mark.unit
def test_world_model_apply_fact_dedupes_identical_evidence() -> None:
    model = InMemoryWorldModel()
    evidence = make_evidence(value=True, confidence=0.9, seconds=1)
    assert model.apply_fact(evidence) is True
    assert model.apply_fact(evidence) is False
    snapshot = model.snapshot(SessionId("session-test"))
    assert snapshot.value_for("healthy") is True


@pytest.mark.unit
def test_world_model_detects_conflicting_values_across_evidence() -> None:
    model = InMemoryWorldModel()
    older = make_evidence(value=False, confidence=0.99, seconds=1)
    newer = make_evidence(value=True, confidence=0.7, seconds=2)
    model.apply_fact(older)
    model.apply_fact(newer)
    snapshot = model.snapshot(SessionId("session-test"))

    assert snapshot.value_for("healthy") is False
    assert snapshot.facts[0].evidence_ids == (older.id, newer.id)
    assert snapshot.conflicting_facts() != ()
    history = snapshot.fact_history_for("deployment/example", "healthy")
    assert history is not None
    assert history.conflicting is True


@pytest.mark.unit
def test_world_model_apply_entity_merges_attributes_and_evidence_ids() -> None:
    model = InMemoryWorldModel()
    first = WorldEntity(
        EntityId("pod/a"), "Pod", immutable_json({"ready": True}), (EvidenceId("e1"),)
    )
    second = WorldEntity(
        EntityId("pod/a"), "Pod", immutable_json({"restarts": 2}), (EvidenceId("e2"),)
    )
    assert model.apply_entity(SessionId("session-test"), first) is True
    assert model.apply_entity(SessionId("session-test"), second) is True
    snapshot = model.snapshot(SessionId("session-test"))
    entity = snapshot.entity_for("pod/a")
    assert entity is not None
    assert entity.attributes == {"ready": True, "restarts": 2}
    assert entity.evidence_ids == ("e1", "e2")


@pytest.mark.unit
def test_world_model_apply_entity_returns_false_when_unchanged() -> None:
    model = InMemoryWorldModel()
    entity = WorldEntity(EntityId("pod/a"), "Pod", immutable_json({"ready": True}))
    assert model.apply_entity(SessionId("session-test"), entity) is True
    assert model.apply_entity(SessionId("session-test"), entity) is False


@pytest.mark.unit
def test_world_model_apply_relation_dedupes_evidence_ids() -> None:
    model = InMemoryWorldModel()
    relation = WorldRelation(EntityId("pod/a"), "owns", EntityId("pod/b"), (EvidenceId("e1"),))
    assert model.apply_relation(SessionId("session-test"), relation) is True
    assert model.apply_relation(SessionId("session-test"), relation) is False
    reasserted = WorldRelation(EntityId("pod/a"), "owns", EntityId("pod/b"), (EvidenceId("e2"),))
    assert model.apply_relation(SessionId("session-test"), reasserted) is True
    snapshot = model.snapshot(SessionId("session-test"))
    assert snapshot.relations_for(source="pod/a")[0].evidence_ids == ("e1", "e2")


@pytest.mark.unit
def test_world_model_forget_removes_session_data() -> None:
    model = InMemoryWorldModel()
    owned = make_evidence(value=True, confidence=0.9, seconds=1, session=SessionId("owned"))
    other = make_evidence(value=False, confidence=0.9, seconds=1, session=SessionId("other"))
    model.apply_fact(owned)
    model.apply_fact(other)
    model.forget(SessionId("owned"))

    assert model.snapshot(SessionId("owned")).facts == ()
    assert model.snapshot(SessionId("other")).value_for("healthy") is False


@pytest.mark.unit
def test_world_model_rebuild_requires_at_least_one_updater() -> None:
    model = InMemoryWorldModel()
    with pytest.raises(ValueError, match="at least one updater"):
        model.rebuild(SessionId("session-test"), (), ())


@pytest.mark.unit
def test_world_model_rebuild_orders_evidence_and_resets_state() -> None:
    model = InMemoryWorldModel()
    updater = FactWorldUpdater()
    newer = make_evidence(value="Deployment", confidence=0.9, seconds=2, claim="kind")
    older = make_evidence(value=False, confidence=0.9, seconds=1, claim="healthy")
    model.apply_fact(older)
    model.apply_fact(newer)
    model.rebuild(SessionId("session-test"), (older, newer), (updater,))

    snapshot = model.snapshot(SessionId("session-test"))
    assert [fact.subject for fact in snapshot.facts] == [
        "deployment/example",
        "deployment/example",
    ]
    assert snapshot.entity_for("deployment/example") is not None


@pytest.mark.unit
def test_world_model_snapshot_filters_by_subjects_and_claims() -> None:
    model = InMemoryWorldModel()
    model.apply_fact(make_evidence(value=True, confidence=0.9, seconds=1))
    model.apply_fact(make_evidence(value="Deployment", confidence=0.9, seconds=2, claim="kind"))
    snapshot = model.snapshot(SessionId("session-test"), subjects=("deployment/example",))
    assert len(snapshot.facts) == 2
    filtered = model.snapshot(SessionId("session-test"), claims=("kind",))
    assert [fact.claim for fact in filtered.facts] == ["kind"]


@pytest.mark.unit
def test_fact_world_updater_projects_facts_entities_and_relations() -> None:
    model = InMemoryWorldModel()
    updater = FactWorldUpdater()
    kind = make_evidence(value="Deployment", confidence=0.9, seconds=1, claim="kind")
    relation = make_evidence(
        value=["pod/a", "pod/b"],
        confidence=0.9,
        seconds=2,
        claim="relation:owns",
    )
    assert updater.apply(model, kind) is True
    assert updater.apply(model, relation) is True
    snapshot = model.snapshot(SessionId("session-test"))

    entity = snapshot.entity_for("deployment/example")
    assert entity is not None
    assert entity.kind == "Deployment"
    assert {item.target for item in snapshot.relations_for(source="deployment/example")} == {
        EntityId("pod/a"),
        EntityId("pod/b"),
    }
    assert updater.apply(model, kind) is False


@pytest.mark.unit
def test_world_fact_evidence_is_frozen_and_constructible() -> None:
    evidence = WorldFactEvidence(
        EvidenceId("e1"), True, 0.9, datetime(2026, 1, 1, tzinfo=UTC), "test"
    )
    assert evidence.evidence_id == "e1"
    assert evidence.value is True
