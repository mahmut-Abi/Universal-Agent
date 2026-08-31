from __future__ import annotations

from datetime import UTC, datetime

from universal_agent.core import (
    ActionId,
    AgentState,
    Goal,
    GoalId,
    JsonValue,
    ObservationId,
    SessionId,
    Task,
    TaskId,
    utc_now,
)
from universal_agent.evidence import Evidence, InMemoryEvidenceStore
from universal_agent.world import CrossDomainWorldModel
from universal_agent.world.cross_domain import WorldFactMergeStrategy, WorldMergePolicy
from universal_agent.world.model import InMemoryWorldModel
from universal_agent.world.models import (
    EntityId,
)

FIXED_AT = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def make_state() -> AgentState:
    return AgentState(
        session_id=SessionId("session-1"),
        goal=Goal("test goal", success_criteria=(), id=GoalId("goal-1")),
        current_task=Task("test task", required_criteria=(), id=TaskId("task-1")),
    )


def make_evidence(
    session_id: SessionId,
    subject: str,
    claim: str,
    value: JsonValue,
    domain_name: str = "domain-a",
    domain_version: str = "1.0.0",
    confidence: float = 1.0,
    source: str = "tool:test",
    observed_at: datetime | None = None,
) -> Evidence:
    return Evidence(
        session_id=session_id,
        task_id=TaskId("task-1"),
        action_id=ActionId("action-1"),
        observation_id=ObservationId("observation-1"),
        subject=subject,
        claim=claim,
        value=value,
        source=source,
        confidence=confidence,
        domain_name=domain_name,
        domain_version=domain_version,
        observed_at=observed_at or utc_now(),
    )


def test_cross_domain_merge_no_conflict() -> None:
    """Two domains agree on the same fact."""
    store = InMemoryEvidenceStore()

    # Domain A says pod is Running
    ev_a = make_evidence(SessionId("session-1"), "pod-1", "status", "Running", "domain-a", "1.0.0")
    # Domain B also says pod is Running (same value)
    ev_b = make_evidence(SessionId("session-1"), "pod-1", "status", "Running", "domain-b", "1.0.0")

    store.add(ev_a)
    store.add(ev_b)

    model = CrossDomainWorldModel(InMemoryWorldModel(), store)
    result = model.merged_snapshot(SessionId("session-1"))

    # Should have one fact, no conflicts
    assert len(result.snapshot.facts) == 1
    fact = result.snapshot.facts[0]
    assert fact.subject == "pod-1"
    assert fact.claim == "status"
    assert fact.value == "Running"
    assert len(result.conflicts) == 0


def test_cross_domain_merge_conflict_detected() -> None:
    """Two domains disagree on the same fact -> conflict detected."""
    store = InMemoryEvidenceStore()

    # Domain A says pod is Running
    ev_a = make_evidence(SessionId("session-1"), "pod-1", "status", "Running", "domain-a", "1.0.0")
    # Domain B says pod is CrashLoopBackOff
    ev_b = make_evidence(
        SessionId("session-1"), "pod-1", "status", "CrashLoopBackOff", "domain-b", "1.0.0"
    )

    store.add(ev_a)
    store.add(ev_b)

    model = CrossDomainWorldModel(InMemoryWorldModel(), store)
    result = model.merged_snapshot(SessionId("session-1"))

    # Should have one fact (winner chosen by confidence/recency)
    assert len(result.snapshot.facts) == 1
    fact = result.snapshot.facts[0]
    assert fact.subject == "pod-1"
    assert fact.claim == "status"
    # Winner is the one with higher confidence (both 1.0) -> later observed_at wins
    # ev_b was created later, so it wins
    assert fact.value == "CrashLoopBackOff"

    # Conflict should be detected
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.subject == "pod-1"
    assert conflict.claim == "status"
    assert set(conflict.values) == {"Running", "CrashLoopBackOff"}
    assert set(conflict.domains) == {"domain-a", "domain-b"}


def test_cross_domain_merge_different_facts_no_conflict() -> None:
    """Different facts from different domains don't conflict."""
    store = InMemoryEvidenceStore()

    # Domain A has pod status
    ev_a = make_evidence(SessionId("session-1"), "pod-1", "status", "Running", "domain-a", "1.0.0")
    # Domain B has deployment replicas
    ev_b = make_evidence(SessionId("session-1"), "deployment-1", "replicas", 3, "domain-b", "1.0.0")

    store.add(ev_a)
    store.add(ev_b)

    model = CrossDomainWorldModel(InMemoryWorldModel(), store)
    result = model.merged_snapshot(SessionId("session-1"))

    assert len(result.snapshot.facts) == 2
    assert len(result.conflicts) == 0


def test_cross_domain_merge_entities() -> None:
    """Entities are merged across domains."""
    store = InMemoryEvidenceStore()
    base_time = utc_now()
    later_time = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC)

    # Domain A creates entity pod-1 with kind Pod
    ev_a = make_evidence(
        SessionId("session-1"), "pod-1", "kind", "Pod", "domain-a", "1.0.0", observed_at=base_time
    )
    # Domain B adds attribute to same entity
    ev_b = make_evidence(
        SessionId("session-1"),
        "pod-1",
        "namespace",
        "default",
        "domain-b",
        "1.0.0",
        observed_at=later_time,
    )

    store.add(ev_a)
    store.add(ev_b)

    model = CrossDomainWorldModel(InMemoryWorldModel(), store)
    result = model.merged_snapshot(SessionId("session-1"))

    assert len(result.snapshot.entities) == 1
    entity = result.snapshot.entities[0]
    assert entity.id == EntityId("pod-1")
    assert entity.kind == "Pod"
    assert entity.attributes.get("namespace") == "default"


def test_cross_domain_merge_relations() -> None:
    """Relations are merged across domains."""
    store = InMemoryEvidenceStore()

    # Domain A says pod-1 relates to deployment-1
    ev_a = make_evidence(
        SessionId("session-1"),
        "pod-1",
        "relation:owned_by",
        "deployment-1",
        "domain-a",
        "1.0.0",
    )
    # Domain B confirms same relation
    ev_b = make_evidence(
        SessionId("session-1"),
        "pod-1",
        "relation:owned_by",
        "deployment-1",
        "domain-b",
        "1.0.0",
    )

    store.add(ev_a)
    store.add(ev_b)

    model = CrossDomainWorldModel(InMemoryWorldModel(), store)
    result = model.merged_snapshot(SessionId("session-1"))

    assert len(result.snapshot.relations) == 1
    rel = result.snapshot.relations[0]
    assert rel.source == EntityId("pod-1")
    assert rel.relation == "owned_by"
    assert rel.target == EntityId("deployment-1")


def test_cross_domain_merge_confidence_based_resolution() -> None:
    """Higher confidence evidence wins in merge."""
    store = InMemoryEvidenceStore()

    # Domain A low confidence
    ev_a = make_evidence(
        SessionId("session-1"),
        "pod-1",
        "status",
        "Running",
        "domain-a",
        "1.0.0",
        confidence=0.5,
    )
    # Domain B high confidence
    ev_b = make_evidence(
        SessionId("session-1"),
        "pod-1",
        "status",
        "CrashLoopBackOff",
        "domain-b",
        "1.0.0",
        confidence=0.9,
    )

    store.add(ev_a)
    store.add(ev_b)

    model = CrossDomainWorldModel(InMemoryWorldModel(), store)
    result = model.merged_snapshot(SessionId("session-1"))

    assert len(result.snapshot.facts) == 1
    fact = result.snapshot.facts[0]
    # Higher confidence should win
    assert fact.value == "CrashLoopBackOff"
    assert fact.confidence == 0.9


def test_cross_domain_merge_empty_evidence() -> None:
    """Empty evidence store returns empty snapshot."""
    model = CrossDomainWorldModel(InMemoryWorldModel(), InMemoryEvidenceStore())
    result = model.merged_snapshot(SessionId("session-1"))

    assert len(result.snapshot.facts) == 0
    assert len(result.snapshot.entities) == 0
    assert len(result.snapshot.relations) == 0
    assert len(result.conflicts) == 0


def test_cross_domain_merge_filters_by_session() -> None:
    """Evidence from other sessions is not included."""
    store = InMemoryEvidenceStore()

    ev_a = make_evidence(SessionId("session-1"), "pod-1", "status", "Running", "domain-a", "1.0.0")
    ev_b = make_evidence(SessionId("session-2"), "pod-2", "status", "Running", "domain-a", "1.0.0")

    store.add(ev_a)
    store.add(ev_b)

    model = CrossDomainWorldModel(InMemoryWorldModel(), store)
    result = model.merged_snapshot(SessionId("session-1"))

    assert len(result.snapshot.facts) == 1
    assert result.snapshot.facts[0].subject == "pod-1"


def test_cross_domain_fact_sources_track_domains() -> None:
    """FactDomainSource tracks which domains contributed to each fact."""
    store = InMemoryEvidenceStore()

    ev_a = make_evidence(SessionId("session-1"), "pod-1", "status", "Running", "domain-a", "1.0.0")
    ev_b = make_evidence(SessionId("session-1"), "pod-1", "status", "Running", "domain-b", "1.0.0")

    store.add(ev_a)
    store.add(ev_b)

    model = CrossDomainWorldModel(InMemoryWorldModel(), store)
    result = model.merged_snapshot(SessionId("session-1"))

    assert len(result.fact_sources) == 1
    source = result.fact_sources[0]
    assert source.subject == "pod-1"
    assert source.claim == "status"
    assert set(source.domains) == {"domain-a", "domain-b"}


def test_cross_domain_merge_subject_filter() -> None:
    """Subject filter limits merged facts."""
    store = InMemoryEvidenceStore()

    ev_a = make_evidence(SessionId("session-1"), "pod-1", "status", "Running", "domain-a", "1.0.0")
    ev_b = make_evidence(SessionId("session-1"), "pod-2", "status", "Running", "domain-a", "1.0.0")

    store.add(ev_a)
    store.add(ev_b)

    model = CrossDomainWorldModel(InMemoryWorldModel(), store)
    result = model.merged_snapshot(SessionId("session-1"), subjects=("pod-1",))

    assert len(result.snapshot.facts) == 1
    assert result.snapshot.facts[0].subject == "pod-1"


def test_cross_domain_merge_claim_filter() -> None:
    """Claim filter limits merged facts."""
    store = InMemoryEvidenceStore()

    ev_a = make_evidence(SessionId("session-1"), "pod-1", "status", "Running", "domain-a", "1.0.0")
    ev_b = make_evidence(
        SessionId("session-1"), "pod-1", "namespace", "default", "domain-a", "1.0.0"
    )

    store.add(ev_a)
    store.add(ev_b)

    model = CrossDomainWorldModel(InMemoryWorldModel(), store)
    result = model.merged_snapshot(SessionId("session-1"), claims=("status",))

    assert len(result.snapshot.facts) == 1
    assert result.snapshot.facts[0].claim == "status"


def test_cross_domain_model_accepts_merge_policy() -> None:
    store = InMemoryEvidenceStore()
    older_high_confidence = make_evidence(
        SessionId("session-1"),
        "pod-1",
        "status",
        "Running",
        "domain-a",
        "1.0.0",
        confidence=0.99,
        observed_at=FIXED_AT,
        source="tool:a",
    )
    newer_low_confidence = make_evidence(
        SessionId("session-1"),
        "pod-1",
        "status",
        "CrashLoopBackOff",
        "domain-b",
        "1.0.0",
        confidence=0.5,
        observed_at=datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC),
        source="tool:b",
    )
    store.add(older_high_confidence)
    store.add(newer_low_confidence)

    model = CrossDomainWorldModel(InMemoryWorldModel(), store)
    result = model.merged_snapshot(
        SessionId("session-1"),
        merge_policy=WorldMergePolicy(
            fact_strategy=WorldFactMergeStrategy.RECENCY_THEN_CONFIDENCE,
        ),
    )

    assert result.snapshot.value_for("status", subject="pod-1") == "CrashLoopBackOff"
