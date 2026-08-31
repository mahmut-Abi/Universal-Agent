from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, NewType
from uuid import uuid4

from rapidfuzz import fuzz
from rapidfuzz.process import extract
from rapidfuzz.utils import default_process

from universal_agent.core import utc_now
from universal_agent.memory.models import MemoryId, MemoryRecord
from universal_agent.memory.store import MemoryStore

ConsolidationActionId = NewType("ConsolidationActionId", str)


def new_consolidation_action_id() -> ConsolidationActionId:
    return ConsolidationActionId(f"consolidate-{uuid4()}")


@dataclass(frozen=True, slots=True)
class ConsolidationAction:
    """A consolidation action to be applied."""

    action_type: str
    target_ids: tuple[MemoryId, ...]
    reason: str
    id: ConsolidationActionId = field(
        default_factory=lambda: ConsolidationActionId(f"consolidate-{uuid4()}")
    )
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class ConsolidationResult:
    """Result of a consolidation run."""

    actions_applied: int
    records_merged: int
    records_archived: int
    conflicts_resolved: int
    actions: tuple[ConsolidationAction, ...]


class MemoryConsolidator:
    """Consolidates memories by deduplicating, merging similar records, and managing versions."""

    def __init__(
        self,
        store: MemoryStore,
        similarity_threshold: float = 0.85,
        max_merge_candidates: int = 100,
    ) -> None:
        self._store = store
        self._similarity_threshold = similarity_threshold
        self._max_merge_candidates = max_merge_candidates

    def consolidate(self, scope: str = "") -> ConsolidationResult:
        """Run a full consolidation pass on memories in the given scope."""
        all_records = self._store.export()
        if scope:
            records = [r for r in all_records if r.scope == scope or r.scope == ""]
        else:
            records = list(all_records)

        actions: list[ConsolidationAction] = []
        records_merged = 0
        records_archived = 0

        # 1. Deduplicate exact duplicates (same subject + content)
        exact_dedup_actions = self._deduplicate_exact(records)
        actions.extend(exact_dedup_actions)
        records_merged += len(exact_dedup_actions)

        # 2. Fuzzy deduplicate similar records
        fuzzy_dedup_actions = self._deduplicate_fuzzy(records)
        actions.extend(fuzzy_dedup_actions)
        records_merged += len(fuzzy_dedup_actions)

        # 3. Merge related records (same subject, different claims)
        merge_actions = self._merge_related(records)
        actions.extend(merge_actions)
        records_merged += len(merge_actions)

        # 4. Resolve version conflicts
        conflict_actions = self._resolve_version_conflicts(records)
        actions.extend(conflict_actions)

        # 5. Archive old/low-confidence records
        archive_actions = self._archive_old(records)
        actions.extend(archive_actions)
        records_archived = len(archive_actions)

        return ConsolidationResult(
            actions_applied=len(actions),
            records_merged=records_merged,
            records_archived=records_archived,
            conflicts_resolved=len(conflict_actions),
            actions=tuple(actions),
        )

    def _deduplicate_exact(self, records: list[MemoryRecord]) -> list[ConsolidationAction]:
        """Remove exact duplicates (same subject + content)."""
        seen: dict[tuple[str, str], MemoryRecord] = {}
        actions: list[ConsolidationAction] = []

        for record in records:
            key = (record.subject, record.content)
            if key in seen:
                existing = seen[key]
                merged = self._merge_records(existing, record)
                self._store.add(merged)
                actions.append(
                    ConsolidationAction(
                        action_type="deduplicate_exact",
                        target_ids=(existing.id, record.id),
                        reason=f"Exact duplicate merged: {record.subject}",
                    )
                )
            else:
                seen[key] = record

        return actions

    def _deduplicate_fuzzy(self, records: list[MemoryRecord]) -> list[ConsolidationAction]:
        """Fuzzy deduplicate similar records using string similarity."""
        if fuzz is None:
            return []

        actions: list[ConsolidationAction] = []
        processed: set[MemoryId] = set()

        for i, record in enumerate(records):
            if record.id in processed:
                continue

            # Find similar records
            candidates = [
                r
                for r in records[i + 1 :]
                if r.id not in processed
                and r.kind == record.kind
                and r.subject == record.subject
                and r.content != record.content
            ]

            if not candidates:
                continue
            # Use fuzzy matching on content
            choices = {i: r.content for i, r in enumerate(candidates)}
            matches = extract(
                record.content,
                choices,
                scorer=fuzz.WRatio,
                processor=default_process,
                score_cutoff=self._similarity_threshold * 100,
                limit=5,
            )

            if not matches:
                continue

            # Merge the most similar
            for _text, score, idx in matches:
                candidate = candidates[idx]
                if candidate.id in processed:
                    continue

                # Merge: keep higher confidence, merge metadata
                merged = self._merge_records(records[i], candidate)
                self._store.add(merged)
                processed.add(candidate.id)

                actions.append(
                    ConsolidationAction(
                        action_type="deduplicate_fuzzy",
                        target_ids=(records[i].id, candidate.id),
                        reason=(
                            f"Fuzzy duplicate merged (score: {score / 100:.2f}): "
                            f"{records[i].subject}"
                        ),
                        metadata={"similarity_score": score / 100.0},
                    )
                )

        return actions

    def _merge_records(self, a: MemoryRecord, b: MemoryRecord) -> MemoryRecord:
        """Merge two records, keeping higher confidence."""
        if b.confidence > a.confidence:
            primary, secondary = b, a
        else:
            primary, secondary = a, b

        return primary.__class__(
            kind=primary.kind,
            subject=primary.subject,
            content=primary.content,
            scope=primary.scope,
            confidence=max(a.confidence, b.confidence),
            source_session_id=primary.source_session_id,
            version=max(a.version, b.version) + 1,
            tags=tuple(set(a.tags) | set(b.tags)),
            source="consolidator",
            metadata={
                **a.metadata,
                **b.metadata,
                "merged_from": str(secondary.id),
                "merge_timestamp": utc_now().isoformat(),
            },
        )

    def _merge_related(self, records: list[MemoryRecord]) -> list[ConsolidationAction]:
        """Merge records with same subject but different claims."""
        # Group by subject
        by_subject: dict[str, list[MemoryRecord]] = {}
        for r in records:
            by_subject.setdefault(r.subject, []).append(r)

        actions: list[ConsolidationAction] = []

        for subject, group in by_subject.items():
            if len(group) <= 1:
                continue

            # Sort by confidence desc
            group.sort(key=lambda r: r.confidence, reverse=True)

            # Merge all into the highest confidence record
            primary = group[0]
            for secondary in group[1:]:
                if secondary.confidence < 0.3:
                    continue  # Skip very low confidence
                if secondary.content == primary.content:
                    continue  # Identical content is handled by dedup stages

                merged = self._merge_records(primary, secondary)
                self._store.add(merged)
                primary = merged  # Chain merges

                actions.append(
                    ConsolidationAction(
                        action_type="merge_related",
                        target_ids=(primary.id,),
                        reason=f"Merged related claims for {subject}",
                        metadata={"merged_count": len(group)},
                    )
                )

        return actions

    def _resolve_version_conflicts(self, records: list[MemoryRecord]) -> list[ConsolidationAction]:
        """Resolve version conflicts (same subject, different versions)."""
        actions: list[ConsolidationAction] = []

        # Group by subject + content, find multiple versions
        by_key: dict[tuple[str, str], list[MemoryRecord]] = {}
        for r in records:
            key = (r.subject, r.content)
            by_key.setdefault(key, []).append(r)

        for _key, group in by_key.items():
            if len(group) <= 1:
                continue

            # Sort by version
            group.sort(key=lambda r: r.version, reverse=True)

            # Keep highest version, archive strictly-older versions.
            # Equal versions are duplicates handled by the dedup stages.
            latest = group[0]
            for old in group[1:]:
                if old.version >= latest.version:
                    continue
                # Archive old version
                archived = old.__class__(
                    kind=old.kind,
                    subject=old.subject,
                    content=old.content,
                    scope=old.scope,
                    confidence=old.confidence,
                    source_session_id=old.source_session_id,
                    version=old.version,
                    tags=(*old.tags, "archived"),
                    source="consolidator",
                    metadata={
                        **old.metadata,
                        "archived": True,
                        "archived_reason": "version_conflict_resolved",
                        "replaced_by": str(latest.id),
                    },
                )
                self._store.add(archived)
                actions.append(
                    ConsolidationAction(
                        action_type="archive_version",
                        target_ids=(old.id,),
                        reason=f"Archived old version of {old.subject}",
                    )
                )

        return actions

    def _archive_old(self, records: list[MemoryRecord]) -> list[ConsolidationAction]:
        """Archive old, low-confidence records."""
        actions: list[ConsolidationAction] = []
        cutoff = utc_now().replace(year=utc_now().year - 1)  # 1 year ago

        for record in records:
            if record.created_at < cutoff and record.confidence < 0.5:
                archived = record.__class__(
                    kind=record.kind,
                    subject=record.subject,
                    content=record.content,
                    scope=record.scope,
                    confidence=record.confidence,
                    source_session_id=record.source_session_id,
                    version=record.version + 1,
                    tags=(*record.tags, "archived"),
                    source="consolidator",
                    metadata={
                        **record.metadata,
                        "archived": True,
                        "archived_reason": "old_low_confidence",
                        "archived_at": utc_now().isoformat(),
                    },
                )
                self._store.add(archived)
                actions.append(
                    ConsolidationAction(
                        action_type="archive",
                        target_ids=(record.id,),
                        reason=f"Archived old low-confidence record: {record.subject}",
                    )
                )

        return actions
