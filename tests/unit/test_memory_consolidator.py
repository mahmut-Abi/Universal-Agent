from __future__ import annotations

from datetime import UTC, datetime, timedelta

from universal_agent.core import utc_now
from universal_agent.memory import (
    ConsolidationAction,
    ConsolidationResult,
    InMemoryMemoryStore,
    MemoryConsolidator,
    MemoryKind,
    MemoryRecord,
)


def make_record(
    *,
    subject: str,
    content: str,
    kind: MemoryKind = MemoryKind.SEMANTIC,
    confidence: float = 1.0,
    version: int = 1,
    scope: str = "",
    created_at: datetime | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        kind=kind,
        subject=subject,
        content=content,
        scope=scope,
        confidence=confidence,
        version=version,
        created_at=created_at or utc_now(),
        source_session_id=None,
    )


def test_consolidator_empty_store() -> None:
    store = InMemoryMemoryStore()
    consolidator = MemoryConsolidator(store)

    result = consolidator.consolidate()

    assert result.actions_applied == 0
    assert result.records_merged == 0
    assert result.records_archived == 0
    assert result.conflicts_resolved == 0
    assert result.actions == ()


def test_consolidator_no_duplicates() -> None:
    store = InMemoryMemoryStore()
    store.add(make_record(subject="a", content="A"))
    store.add(make_record(subject="b", content="B"))
    consolidator = MemoryConsolidator(store)

    result = consolidator.consolidate()

    assert result.actions_applied == 0
    assert len(store.export()) == 2


def test_consolidator_exact_dedup() -> None:
    store = InMemoryMemoryStore()
    store.add(make_record(subject="deployment", content="has 3 replicas"))
    store.add(make_record(subject="deployment", content="has 3 replicas"))
    consolidator = MemoryConsolidator(store)

    result = consolidator.consolidate()

    assert result.records_merged == 1
    assert any(a.action_type == "deduplicate_exact" for a in result.actions)
    dedup = next(a for a in result.actions if a.action_type == "deduplicate_exact")
    assert len(dedup.target_ids) == 2

    # A merged record with a new id was added and tagged as consolidator output
    merged = [r for r in store.export() if r.source == "consolidator"]
    assert len(merged) == 1
    assert merged[0].content == "has 3 replicas"
    assert "merged_from" in merged[0].metadata


def test_consolidator_exact_dedup_keeps_higher_confidence() -> None:
    store = InMemoryMemoryStore()
    store.add(make_record(subject="s", content="same", confidence=0.5, scope="d"))
    store.add(make_record(subject="s", content="same", confidence=0.9, scope="d"))
    consolidator = MemoryConsolidator(store)

    consolidator.consolidate()

    merged = [r for r in store.export() if r.source == "consolidator"]
    assert merged[0].confidence == 0.9


def test_consolidator_fuzzy_dedup() -> None:
    store = InMemoryMemoryStore()
    store.add(make_record(subject="pod", content="The pod is crash-looping on startup"))
    store.add(
        make_record(
            subject="pod",
            content="The pod is crash-looping on startup and never becomes ready",
        )
    )
    consolidator = MemoryConsolidator(store, similarity_threshold=0.5)

    result = consolidator.consolidate()

    assert result.records_merged >= 1
    fuzzy = next(a for a in result.actions if a.action_type == "deduplicate_fuzzy")
    assert "similarity_score" in fuzzy.metadata


def test_consolidator_fuzzy_ignores_unrelated_content() -> None:
    store = InMemoryMemoryStore()
    store.add(make_record(subject="pod", content="pod restarted slowly"))
    store.add(make_record(subject="pod", content="completely unrelated text about weather today"))
    consolidator = MemoryConsolidator(store, similarity_threshold=0.85)

    result = consolidator.consolidate()

    assert all(a.action_type != "deduplicate_fuzzy" for a in result.actions)


def test_consolidator_merge_related_claims() -> None:
    store = InMemoryMemoryStore()
    store.add(make_record(subject="diffy", content="version 0.9", confidence=0.9))
    store.add(make_record(subject="diffy", content="version 0.10", confidence=0.6))
    consolidator = MemoryConsolidator(store)

    result = consolidator.consolidate()

    assert any(a.action_type == "merge_related" for a in result.actions)


def test_consolidator_version_conflict_archives_old() -> None:
    store = InMemoryMemoryStore()
    store.add(make_record(subject="cfg", content="x=1", version=2))
    store.add(make_record(subject="cfg", content="x=1", version=1))
    consolidator = MemoryConsolidator(store)

    result = consolidator.consolidate()

    assert result.conflicts_resolved == 1
    archived = [r for r in store.export() if "archived" in r.tags]
    assert len(archived) == 1
    assert archived[0].version == 1
    assert archived[0].metadata.get("archived_reason") == "version_conflict_resolved"


def test_consolidator_archives_old_low_confidence() -> None:
    store = InMemoryMemoryStore()
    old = datetime.now(UTC) - timedelta(days=400)
    store.add(make_record(subject="stale", content="old fact", confidence=0.3, created_at=old))
    store.add(
        make_record(subject="fresh", content="new fact", confidence=0.9, created_at=utc_now())
    )
    consolidator = MemoryConsolidator(store)

    result = consolidator.consolidate()

    assert result.records_archived == 1
    assert any(a.action_type == "archive" for a in result.actions)
    archived = [r for r in store.export() if "archived" in r.tags]
    assert len(archived) == 1
    assert archived[0].subject == "stale"


def test_consolidator_keeps_old_high_confidence() -> None:
    store = InMemoryMemoryStore()
    old = datetime.now(UTC) - timedelta(days=400)
    store.add(
        make_record(subject="important", content="old but reliable", confidence=0.9, created_at=old)
    )
    consolidator = MemoryConsolidator(store)

    result = consolidator.consolidate()

    assert result.records_archived == 0


def test_consolidator_scope_filter() -> None:
    store = InMemoryMemoryStore()
    store.add(make_record(subject="in_scope", content="dup", scope="app"))
    store.add(make_record(subject="in_scope", content="dup", scope="app"))
    store.add(make_record(subject="other", content="dup", scope="other"))
    store.add(make_record(subject="other", content="dup", scope="other"))
    consolidator = MemoryConsolidator(store)

    result = consolidator.consolidate(scope="app")

    assert result.records_merged == 1
    # Only the app-scoped duplicate was merged
    assert any(a.action_type == "deduplicate_exact" for a in result.actions)


def test_consolidator_result_types() -> None:
    store = InMemoryMemoryStore()
    store.add(make_record(subject="s", content="c"))
    store.add(make_record(subject="s", content="c"))
    consolidator = MemoryConsolidator(store)

    result = consolidator.consolidate()

    assert isinstance(result, ConsolidationResult)
    for action in result.actions:
        assert isinstance(action, ConsolidationAction)
        assert action.reason
