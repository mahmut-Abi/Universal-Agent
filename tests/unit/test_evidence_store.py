from __future__ import annotations

from datetime import UTC, datetime

import pytest

from universal_agent.core import (
    ActionId,
    JsonValue,
    Observation,
    ObservationId,
    ObservationStatus,
    SessionId,
    Task,
    TaskId,
    immutable_json,
)
from universal_agent.evidence import (
    Evidence,
    EvidenceContext,
    EvidenceId,
    EvidenceQuery,
    InMemoryEvidenceStore,
    StructuredEvidenceExtractor,
)

FIXED_AT = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
OTHER_AT = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC)


def make_evidence(
    session_id: str = "session-1",
    task_id: str = "task-1",
    subject: str = "subject",
    claim: str = "claim",
    value: JsonValue = "value",
    evidence_id: str = "evidence-1",
    observed_at: datetime = FIXED_AT,
    confidence: float = 1.0,
) -> Evidence:
    return Evidence(
        SessionId(session_id),
        TaskId(task_id),
        ActionId("action-1"),
        ObservationId("observation-1"),
        subject,
        claim,
        value,
        "cap:tool",
        confidence=confidence,
        id=EvidenceId(evidence_id),
        observed_at=observed_at,
    )


def make_task(task_id: str = "task-1") -> Task:
    return Task("task", required_criteria=(), id=TaskId(task_id))


def test_evidence_constructs_with_defaults_when_valid() -> None:
    evidence = make_evidence()

    assert evidence.subject == "subject"
    assert evidence.claim == "claim"
    assert evidence.value == "value"
    assert evidence.confidence == 1.0
    assert evidence.source == "cap:tool"
    assert evidence.id == EvidenceId("evidence-1")


def test_evidence_rejects_confidence_above_one() -> None:
    with pytest.raises(ValueError, match="evidence confidence must be between zero and one"):
        make_evidence(confidence=1.5)


def test_evidence_rejects_confidence_below_zero() -> None:
    with pytest.raises(ValueError, match="evidence confidence must be between zero and one"):
        make_evidence(confidence=-0.1)


def test_evidence_rejects_empty_subject() -> None:
    with pytest.raises(ValueError, match="evidence subject and claim are required"):
        make_evidence(subject="")


def test_evidence_rejects_empty_claim() -> None:
    with pytest.raises(ValueError, match="evidence subject and claim are required"):
        make_evidence(claim="")


def test_structured_extractor_name_is_fixed() -> None:
    assert StructuredEvidenceExtractor.name == "structured-observation"


def test_structured_extractor_extracts_on_succeeded_observation() -> None:
    observation = Observation(
        id=ObservationId("observation-1"),
        action_id=ActionId("action-1"),
        task_id=TaskId("task-1"),
        source="cap:tool",
        status=ObservationStatus.SUCCEEDED,
        data=immutable_json({"pods": 3, "ready": 2}),
        observed_at=FIXED_AT,
    )
    context = EvidenceContext(SessionId("session-1"), make_task("task-1"), observation)

    extracted = StructuredEvidenceExtractor().extract(context)

    assert len(extracted) == 2
    by_claim = {item.claim: item for item in extracted}
    assert by_claim["pods"].value == 3
    assert by_claim["ready"].value == 2
    assert by_claim["pods"].subject == "cap:tool"
    assert by_claim["pods"].source == "cap:tool"
    assert by_claim["pods"].task_id == TaskId("task-1")
    assert by_claim["pods"].observed_at == FIXED_AT


def test_structured_extractor_skips_non_succeeded_observation() -> None:
    observation = Observation(
        id=ObservationId("observation-1"),
        action_id=ActionId("action-1"),
        task_id=TaskId("task-1"),
        source="cap:tool",
        status=ObservationStatus.FAILED,
        data=immutable_json({"pods": 3}),
        observed_at=FIXED_AT,
    )
    context = EvidenceContext(SessionId("session-1"), make_task("task-1"), observation)

    assert StructuredEvidenceExtractor().extract(context) == ()


def test_structured_extractor_skips_empty_observation_data() -> None:
    observation = Observation(
        id=ObservationId("observation-1"),
        action_id=ActionId("action-1"),
        task_id=TaskId("task-1"),
        source="cap:tool",
        status=ObservationStatus.SUCCEEDED,
        data=immutable_json({}),
        observed_at=FIXED_AT,
    )
    context = EvidenceContext(SessionId("session-1"), make_task("task-1"), observation)

    assert StructuredEvidenceExtractor().extract(context) == ()


def test_evidence_query_fields_are_readable() -> None:
    query = EvidenceQuery(
        SessionId("session-1"),
        task_id=TaskId("task-1"),
        subject="subject",
        claim="claim",
        limit=5,
    )

    assert query.session_id == SessionId("session-1")
    assert query.task_id == TaskId("task-1")
    assert query.subject == "subject"
    assert query.claim == "claim"
    assert query.limit == 5


def test_in_memory_store_add_returns_true_then_false_on_duplicate() -> None:
    store = InMemoryEvidenceStore()
    evidence = make_evidence()

    assert store.add(evidence) is True
    assert store.add(evidence) is False
    assert store.add(make_evidence(evidence_id="evidence-1")) is False


def test_in_memory_store_query_filters_by_task_subject_claim_and_limit() -> None:
    store = InMemoryEvidenceStore()
    store.add(make_evidence(task_id="task-1", subject="dep", claim="status", evidence_id="e1"))
    store.add(make_evidence(task_id="task-1", subject="dep", claim="phase", evidence_id="e2"))
    store.add(make_evidence(task_id="task-2", subject="dep", claim="status", evidence_id="e3"))

    by_task = store.query(EvidenceQuery(SessionId("session-1"), task_id=TaskId("task-1")))
    assert {item.id for item in by_task} == {EvidenceId("e1"), EvidenceId("e2")}

    by_subject = store.query(EvidenceQuery(SessionId("session-1"), subject="dep"))
    assert len(by_subject) == 3

    by_claim = store.query(
        EvidenceQuery(SessionId("session-1"), task_id=TaskId("task-1"), claim="status")
    )
    assert {item.id for item in by_claim} == {EvidenceId("e1")}

    limited = store.query(EvidenceQuery(SessionId("session-1"), subject="dep", limit=2))
    assert len(limited) == 2


def test_in_memory_store_query_returns_newest_first() -> None:
    store = InMemoryEvidenceStore()
    store.add(make_evidence(evidence_id="old", observed_at=FIXED_AT))
    store.add(make_evidence(evidence_id="new", observed_at=OTHER_AT))

    result = store.query(EvidenceQuery(SessionId("session-1")))

    assert result[0].id == EvidenceId("new")
    assert result[1].id == EvidenceId("old")


def test_in_memory_store_query_is_session_scoped() -> None:
    store = InMemoryEvidenceStore()
    store.add(make_evidence(session_id="session-1", evidence_id="s1"))
    store.add(make_evidence(session_id="session-2", evidence_id="s2"))

    result = store.query(EvidenceQuery(SessionId("session-1")))

    assert {item.id for item in result} == {EvidenceId("s1")}


def test_in_memory_store_export_sorts_ascending_by_observed_at() -> None:
    store = InMemoryEvidenceStore()
    store.add(make_evidence(evidence_id="new", observed_at=OTHER_AT))
    store.add(make_evidence(evidence_id="old", observed_at=FIXED_AT))

    exported = store.export(SessionId("session-1"))

    assert [item.id for item in exported] == [EvidenceId("old"), EvidenceId("new")]


def test_in_memory_store_replace_swaps_session_evidence() -> None:
    store = InMemoryEvidenceStore()
    store.add(make_evidence(session_id="session-1", evidence_id="old-1"))
    store.add(make_evidence(session_id="session-2", evidence_id="keep-2"))

    store.replace(
        SessionId("session-1"),
        (make_evidence(session_id="session-1", evidence_id="new-1"),),
    )

    assert {item.id for item in store.export(SessionId("session-1"))} == {EvidenceId("new-1")}
    assert {item.id for item in store.export(SessionId("session-2"))} == {EvidenceId("keep-2")}


def test_in_memory_store_add_generates_unique_id_when_omitted() -> None:
    store = InMemoryEvidenceStore()
    first = Evidence(
        SessionId("session-1"),
        TaskId("task-1"),
        ActionId("action-1"),
        ObservationId("observation-1"),
        "subject",
        "claim",
        "value",
        "cap:tool",
    )
    second = Evidence(
        SessionId("session-1"),
        TaskId("task-1"),
        ActionId("action-1"),
        ObservationId("observation-1"),
        "subject",
        "claim",
        "value",
        "cap:tool",
    )

    assert store.add(first) is True
    assert first.id != second.id
    assert store.add(second) is True
