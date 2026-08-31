from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from rapidfuzz import fuzz, process, utils

from universal_agent.memory.models import MemoryQuery, MemoryRecord
from universal_agent.memory.store import MemoryStore


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    """Pure-string retrieval input.

    Deliberately avoids world / capability types so the memory package never
    imports them: retrieval is a string-matching concern, and keeping the
    boundary in plain strings prevents a cycle that would let memory reach into
    runtime state.
    """

    goal_description: str
    task_description: str
    subjects: tuple[str, ...] = ()
    scope: str | None = None
    limit: int | None = None


class MemoryRetriever(Protocol):
    """Recall step: widen the store to a candidate set."""

    def retrieve(self, request: RetrievalRequest) -> tuple[MemoryRecord, ...]: ...


class RelevanceFilter(Protocol):
    """Filter step: score candidates and keep the relevant ones."""

    def filter(
        self,
        records: Sequence[MemoryRecord],
        request: RetrievalRequest,
    ) -> tuple[MemoryRecord, ...]: ...


@dataclass(frozen=True, slots=True)
class StoreMemoryRetriever:
    _store: MemoryStore
    recall_limit: int = 32

    def retrieve(self, request: RetrievalRequest) -> tuple[MemoryRecord, ...]:
        # Recall broadly by scope (and kinds when given): subjects are runtime
        # entities that rarely equal a memory's topic, so they must not gate
        # recall. They feed the relevance filter as soft scoring signal instead.
        return self._store.query(
            MemoryQuery(
                kinds=(),
                subjects=(),
                scope=request.scope,
                limit=self.recall_limit,
            )
        )


@dataclass(frozen=True, slots=True)
class KeywordRelevanceFilter:
    threshold: float = 0.45
    limit: int = 8

    def filter(
        self,
        records: Sequence[MemoryRecord],
        request: RetrievalRequest,
    ) -> tuple[MemoryRecord, ...]:
        query = _query_text(request)
        if not query.strip():
            return ()

        choices = {
            index: text
            for index, record in enumerate(records)
            if (text := _record_text(record).strip())
        }
        if not choices:
            return ()

        scored: list[tuple[float, MemoryRecord]] = []
        matches = process.extract(
            query,
            choices,
            scorer=fuzz.WRatio,
            processor=utils.default_process,
            score_cutoff=self.threshold * 100.0,
            limit=None,
        )
        for _text, raw_score, index in matches:
            score = (raw_score / 100.0) * records[index].confidence
            if score >= self.threshold:
                scored.append((score, records[index]))

        scored.sort(key=lambda pair: (pair[0], pair[1].created_at), reverse=True)
        kept = [record for _, record in scored[: self.limit]]
        return tuple(kept)


def similarity_score(query: str, text: str) -> float:
    """Return a 0.0-1.0 WRatio similarity score between two strings."""
    if not query.strip() or not text.strip():
        return 0.0
    return fuzz.WRatio(query, text) / 100.0


def _query_text(request: RetrievalRequest) -> str:
    # Runtime subjects are soft signal: they influence relevance ranking without
    # gating the store recall step.
    return " ".join(
        part.strip()
        for part in (
            request.goal_description,
            request.task_description,
            *request.subjects,
        )
        if part.strip()
    )


def _record_text(record: MemoryRecord) -> str:
    return f"{record.subject} {record.content}"
