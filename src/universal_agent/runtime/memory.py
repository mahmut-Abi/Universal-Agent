from __future__ import annotations

from universal_agent.core import ExecutionStatus
from universal_agent.core.models import AgentState
from universal_agent.domain import RuntimeComponents
from universal_agent.memory import MemoryKind, MemoryRecord, RetrievalRequest
from universal_agent.memory.models import MemoryQuery
from universal_agent.memory.retrieval import similarity_score
from universal_agent.runtime.session import SessionRuntimeState
from universal_agent.runtime.transitions import Transition


class MemoryConsultant:
    """Advisory memory path for the runtime loop.

    Memory enters context only through `recall`, and is written back only at
    terminal transitions through `record_episodic`. It never becomes evidence,
    never updates the world model, and never reaches the evaluator.
    """

    def __init__(
        self,
        components: RuntimeComponents,
        max_episodic_recall: int = 3,
        episodic_threshold: float = 0.4,
    ) -> None:
        self._scope = components.memory_scope
        self._retriever = components.memory_retriever
        self._filter = components.memory_filter
        self._store = components.memory_store
        self._max_episodic_recall = max_episodic_recall
        self._episodic_threshold = episodic_threshold

    def recall(self, session: SessionRuntimeState) -> tuple[MemoryRecord, ...]:
        state = session.state  # AgentState
        request = RetrievalRequest(
            goal_description=state.goal.description,
            task_description=state.current_task.description,
            subjects=tuple(fact.subject for fact in session.world().facts),
            scope=self._scope,
        )
        candidates = self._retriever.retrieve(request)
        filtered = self._filter.filter(candidates, request)

        # Cross-session episodic recall: pull comparable past outcomes.
        # We intentionally exclude EPISODIC records from the general filter so
        # they get their own channel — this prevents semantic/procedural filler
        # from crowding them out, and makes the "past outcome" signal explicit.
        episodic_ids = {r.id for r in filtered if r.kind is MemoryKind.EPISODIC}
        general = tuple(r for r in filtered if r.kind is not MemoryKind.EPISODIC)

        episodic = self._recall_episodic_others(state, general)
        # Avoid duplicating any episodic that already appeared in general.
        dedup_episodic = tuple(r for r in episodic if r.id not in episodic_ids)

        return general + dedup_episodic

    def _recall_episodic_others(
        self, state: AgentState, general: tuple[MemoryRecord, ...]
    ) -> tuple[MemoryRecord, ...]:
        """Recall EPISODIC records from *other* sessions in the same scope.

        Scoring is based on similarity to the current goal + task description.
        Records are labelled with metadata so the compiled context can
        distinguish cross-session episodes.
        """
        query = f"{state.goal.description} {state.current_task.description}".strip()
        if not query:
            return ()

        current_session = state.session_id
        # Fetch broadly; filter for EPISODIC kind in Python to avoid mypy
        # StrEnum literal resolution issues with MemoryQuery.kinds.
        all_past = self._store.query(
            MemoryQuery(
                scope=self._scope,
                limit=self._max_episodic_recall * 4,
            )
        )
        past = [r for r in all_past if r.kind is MemoryKind.EPISODIC]

        scored: list[tuple[float, MemoryRecord]] = []
        for record in past:
            # Skip the current session's own episode.
            if record.source_session_id == current_session:
                continue
            text = f"{record.subject} {record.content}"
            score = similarity_score(query, text)
            if score >= self._episodic_threshold:
                scored.append((score, record))

        # Sort by similarity (higher first), then by recency.
        scored.sort(key=lambda p: (p[0], p[1].created_at), reverse=True)
        kept: list[MemoryRecord] = []
        for _score, record in scored[: self._max_episodic_recall]:
            # Tag the record so consumers can identify cross-session episodes.
            metadata = dict(record.metadata)
            metadata["recall_channel"] = "cross_session_episodic"
            metadata["goal_similarity"] = round(_score, 3)
            # Record is frozen; create a copy with updated metadata.
            updated = MemoryRecord(
                kind=record.kind,
                subject=record.subject,
                content=record.content,
                scope=record.scope,
                confidence=record.confidence,
                source_session_id=record.source_session_id,
                id=record.id,
                created_at=record.created_at,
                version=record.version,
                tags=record.tags,
                source=record.source,
                metadata=metadata,
            )
            kept.append(updated)

        return tuple(kept)

    def recall_episodic(self, session: SessionRuntimeState) -> tuple[MemoryRecord, ...]:
        """Return cross-session episodic records for the given session.

        This is a public helper that mirrors the episodic channel inside
        `recall()` but can be used independently (e.g. by a context provider
        that wants only the past-outcome signal).
        """
        state = session.state  # AgentState
        query = f"{state.goal.description} {state.current_task.description}".strip()
        if not query:
            return ()

        current_session = state.session_id
        all_past = self._store.query(
            MemoryQuery(
                scope=self._scope,
                limit=self._max_episodic_recall * 4,
            )
        )
        past = [r for r in all_past if r.kind is MemoryKind.EPISODIC]

        scored: list[tuple[float, MemoryRecord]] = []
        for record in past:
            if record.source_session_id == current_session:
                continue
            text = f"{record.subject} {record.content}"
            score = similarity_score(query, text)
            if score >= self._episodic_threshold:
                scored.append((score, record))

        scored.sort(key=lambda p: (p[0], p[1].created_at), reverse=True)
        kept: list[MemoryRecord] = []
        for _score, record in scored[: self._max_episodic_recall]:
            metadata = dict(record.metadata)
            metadata["recall_channel"] = "cross_session_episodic"
            metadata["goal_similarity"] = round(_score, 3)
            updated = MemoryRecord(
                kind=record.kind,
                subject=record.subject,
                content=record.content,
                scope=record.scope,
                confidence=record.confidence,
                source_session_id=record.source_session_id,
                id=record.id,
                created_at=record.created_at,
                version=record.version,
                tags=record.tags,
                source=record.source,
                metadata=metadata,
            )
            kept.append(updated)

        return tuple(kept)

    def record_episodic(self, session: SessionRuntimeState, transition: Transition) -> None:
        """Write a single episodic record at a terminal transition.

        WAITING is not a terminal state: the session may resume, so there is no
        settled experience to record yet. Only COMPLETED / FAILED produce an
        episodic memory, which future sessions of the same runtime may recall.
        """
        result = transition.result
        if result.status is ExecutionStatus.WAITING:
            return
        state = session.state  # AgentState
        content = f"Goal '{state.goal.description}' ended as {result.status.value}: {result.reason}"
        record = MemoryRecord(
            kind=MemoryKind.EPISODIC,
            subject=f"session {state.session_id}",
            content=content,
            scope=self._scope or "",
            confidence=1.0,
            source_session_id=state.session_id,
        )
        self._store.add(record)
