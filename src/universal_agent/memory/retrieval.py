from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from universal_agent.memory.models import MemoryQuery, MemoryRecord
from universal_agent.memory.store import MemoryStore

_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "was",
        "were",
        "will",
        "with",
        "this",
        "these",
        "those",
    }
)


def _tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in text.lower().split():
        cleaned = "".join(ch for ch in raw if ch.isalnum())
        if cleaned and cleaned not in _STOP_WORDS:
            tokens.add(cleaned)
    return tokens


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
    threshold: float = 0.05
    limit: int = 8

    def filter(
        self,
        records: Sequence[MemoryRecord],
        request: RetrievalRequest,
    ) -> tuple[MemoryRecord, ...]:
        query_tokens: set[str] = set()
        query_tokens |= _tokens(request.goal_description)
        query_tokens |= _tokens(request.task_description)
        # Runtime subjects (world entities) are soft signal: tokenize them so a
        # memory mentioning 'workload' surfaces when the world reports one,
        # without subjects ever gating recall.
        for subject in request.subjects:
            query_tokens |= _tokens(subject)
        if not query_tokens:
            return ()

        scored: list[tuple[float, MemoryRecord]] = []
        for record in records:
            record_tokens = _tokens(record.subject) | _tokens(record.content)
            if not record_tokens:
                continue
            overlap = len(query_tokens & record_tokens)
            score = (overlap / len(query_tokens)) * record.confidence
            if score >= self.threshold:
                scored.append((score, record))

        scored.sort(key=lambda pair: (pair[0], pair[1].created_at), reverse=True)
        kept = [record for _, record in scored[: self.limit]]
        return tuple(kept)
