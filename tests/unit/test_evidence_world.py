from datetime import UTC, datetime, timedelta

from universal_agent.core import (
    Observation,
    ObservationStatus,
    SessionId,
    Task,
    immutable_json,
    new_action_id,
    new_observation_id,
)
from universal_agent.evidence import Evidence, EvidenceQuery, InMemoryEvidenceStore
from universal_agent.world import InMemoryWorldModel


def make_evidence(*, value: bool, confidence: float, seconds: int) -> Evidence:
    task = Task("Inspect", ("healthy",))
    action_id = new_action_id()
    observation = Observation(
        new_observation_id(),
        action_id,
        task.id,
        "test",
        ObservationStatus.SUCCEEDED,
        immutable_json({"healthy": value}),
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds),
    )
    return Evidence(
        SessionId("session-test"),
        task.id,
        action_id,
        observation.id,
        "deployment/example",
        "healthy",
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
