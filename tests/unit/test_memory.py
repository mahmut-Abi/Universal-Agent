from __future__ import annotations

from datetime import UTC, datetime

import pytest

from universal_agent.context import DomainContextProvider
from universal_agent.core import (
    CapabilityDefinition,
    DomainManifest,
    DomainMetadata,
    JsonMapping,
    immutable_json,
)
from universal_agent.domain import DomainLoader, DomainValidationError, RuntimeBuilder
from universal_agent.domains.kubernetes import KubernetesDomain
from universal_agent.evaluation import CriteriaEvaluator, Evaluator
from universal_agent.evidence import EvidenceExtractor
from universal_agent.memory import (
    InMemoryMemoryStore,
    KeywordRelevanceFilter,
    MemoryKind,
    MemoryQuery,
    MemoryRecord,
    RetrievalRequest,
    StoreMemoryRetriever,
)
from universal_agent.policy import Policy
from universal_agent.recovery import RecoveryRule
from universal_agent.tasks import TaskExpander
from universal_agent.tools import Tool
from universal_agent.world import WorldUpdater


def make_record(
    *,
    kind: MemoryKind = MemoryKind.SEMANTIC,
    subject: str = "pod health",
    content: str = "check pods before logs",
    scope: str = "",
    confidence: float = 1.0,
) -> MemoryRecord:
    return MemoryRecord(kind, subject, content, scope, confidence)


def test_memory_record_validates_confidence_and_fields() -> None:
    with pytest.raises(ValueError, match="confidence"):
        MemoryRecord(MemoryKind.SEMANTIC, "s", "c", confidence=1.5)
    with pytest.raises(ValueError, match="subject"):
        MemoryRecord(MemoryKind.SEMANTIC, "", "c")


def test_store_round_trip_dedup_and_stable_export() -> None:
    store = InMemoryMemoryStore()
    record = make_record()
    assert store.add(record) is True
    assert store.add(record) is False
    second = make_record(subject="other")
    store.add(second)
    exported = store.export()
    assert len(exported) == 2
    assert exported == tuple(sorted((record, second), key=lambda r: (r.created_at, str(r.id))))


def test_query_filters_by_kind_subject_scope_limit() -> None:
    store = InMemoryMemoryStore()
    semantic = make_record(kind=MemoryKind.SEMANTIC, subject="alpha", scope="kubernetes")
    procedural = make_record(kind=MemoryKind.PROCEDURAL, subject="beta", scope="kubernetes")
    global_record = make_record(kind=MemoryKind.SEMANTIC, subject="gamma", scope="")
    for record in (semantic, procedural, global_record):
        store.add(record)

    assert store.query(MemoryQuery(kinds=(MemoryKind.SEMANTIC,))) == (
        semantic,
        global_record,
    )
    assert {r.subject for r in store.query(MemoryQuery(subjects=("beta",)))} == {"beta"}
    # A scoped query also admits globally-scoped records (empty scope).
    scoped = store.query(MemoryQuery(scope="kubernetes"))
    assert global_record in scoped and semantic in scoped
    assert len(store.query(MemoryQuery(limit=1))) == 1


def test_retriever_isolates_by_scope_recall() -> None:
    store = InMemoryMemoryStore()
    a = make_record(subject="pod", content="alpha detail", scope="kubernetes")
    b = make_record(subject="node", content="beta detail", scope="kubernetes")
    foreign = make_record(subject="billing", content="gamma detail", scope="finance")
    for record in (a, b, foreign):
        store.add(record)
    retriever = StoreMemoryRetriever(store)
    # Recall is gated by scope, not by subject; runtime subjects are entities
    # that rarely match a memory topic, so they feed only the relevance filter.
    assert len(retriever.retrieve(RetrievalRequest("g", "t", scope="kubernetes"))) == 2
    assert retriever.retrieve(RetrievalRequest("g", "t", scope="finance")) == (foreign,)
    # No scope still admits everything (subject filter stays unused at recall).
    assert len(retriever.retrieve(RetrievalRequest("g", "t"))) == 3


def test_relevance_filter_scores_thresholds_and_truncates() -> None:
    store = InMemoryMemoryStore()
    relevant = make_record(subject="pod health", content="check pods readiness", confidence=0.9)
    noise = make_record(subject="billing", content="invoice totals", confidence=0.1)
    store.add(relevant)
    store.add(noise)
    retriever = StoreMemoryRetriever(store)
    candidates = retriever.retrieve(RetrievalRequest("diagnose pod health", "check pods"))
    filtered = KeywordRelevanceFilter(limit=1).filter(
        candidates, RetrievalRequest("diagnose pod health", "check pods")
    )
    assert filtered == (relevant,)
    # No overlapping tokens -> nothing survives.
    assert (
        KeywordRelevanceFilter().filter(
            candidates, RetrievalRequest("completely unrelated query", "no overlap")
        )
        == ()
    )


def test_relevance_filter_uses_library_fuzzy_matching_for_word_variants() -> None:
    record = make_record(
        subject="HTTP probe failure",
        content="Check readiness-probe events before restarting the workload.",
    )

    filtered = KeywordRelevanceFilter().filter(
        (record,),
        RetrievalRequest(
            "diagnose http probe failing on api pod",
            "inspect readiness probe",
        ),
    )

    assert filtered == (record,)


def test_relevance_filter_applies_confidence_after_fuzzy_score() -> None:
    record = make_record(
        subject="HTTP probe failure",
        content="Check readiness-probe events before restarting the workload.",
        confidence=0.2,
    )

    assert (
        KeywordRelevanceFilter().filter(
            (record,),
            RetrievalRequest(
                "diagnose http probe failing on api pod",
                "inspect readiness probe",
            ),
        )
        == ()
    )


def test_query_limit_returns_most_recent_records() -> None:
    store = InMemoryMemoryStore()
    oldest = MemoryRecord(
        MemoryKind.SEMANTIC,
        "oldest",
        "content",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    middle = MemoryRecord(
        MemoryKind.SEMANTIC,
        "middle",
        "content",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    newest = MemoryRecord(
        MemoryKind.SEMANTIC,
        "newest",
        "content",
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
    )
    for record in (oldest, middle, newest):
        store.add(record)

    assert store.query(MemoryQuery(limit=2)) == (newest, middle)


def test_runtime_builder_seeds_domain_memories_once() -> None:
    class Backend:
        async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
            return immutable_json({})

    store = InMemoryMemoryStore()
    builder = RuntimeBuilder(memory_store_factory=lambda: store)
    domain = DomainLoader().load(KubernetesDomain(Backend()))

    builder.build(domain)
    builder.build(domain)

    assert [record.subject for record in store.export()] == [
        "unhealthy workload triage",
        "kubernetes readiness",
    ]


def test_domain_loader_rejects_episodic_memory() -> None:
    class EpisodicDomain:
        @property
        def manifest(self) -> DomainManifest:
            return DomainManifest(
                "agent.nantian.dev/v1alpha1",
                "Domain",
                DomainMetadata("episodic-test", "0.1.0", ""),
                (),
                (),
                ("criteria",),
            )

        def capabilities(self) -> tuple[CapabilityDefinition, ...]:
            return ()

        def tools(self) -> tuple[Tool, ...]:
            return ()

        def policies(self) -> tuple[Policy, ...]:
            return ()

        def evaluators(self) -> tuple[Evaluator, ...]:
            return (CriteriaEvaluator(),)

        def context_providers(self) -> tuple[DomainContextProvider, ...]:
            return ()

        def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
            return ()

        def world_updaters(self) -> tuple[WorldUpdater, ...]:
            return ()

        def task_expanders(self) -> tuple[TaskExpander, ...]:
            return ()

        def recovery_rules(self) -> tuple[RecoveryRule, ...]:
            return ()

        def memories(self) -> tuple[MemoryRecord, ...]:
            return (MemoryRecord(MemoryKind.EPISODIC, "past", "never happened"),)

    with pytest.raises(DomainValidationError, match="episodic"):
        DomainLoader().load(EpisodicDomain())
