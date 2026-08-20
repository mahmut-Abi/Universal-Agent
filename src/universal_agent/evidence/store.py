from __future__ import annotations

from typing import Protocol

from universal_agent.core import SessionId
from universal_agent.evidence.models import Evidence, EvidenceId, EvidenceQuery


class EvidenceStore(Protocol):
    def add(self, evidence: Evidence) -> bool: ...

    def query(self, query: EvidenceQuery) -> tuple[Evidence, ...]: ...

    def export(self, session_id: SessionId) -> tuple[Evidence, ...]: ...

    def replace(self, session_id: SessionId, evidence: tuple[Evidence, ...]) -> None: ...


class InMemoryEvidenceStore:
    def __init__(self) -> None:
        self._evidence: dict[EvidenceId, Evidence] = {}

    def add(self, evidence: Evidence) -> bool:
        if evidence.id in self._evidence:
            return False
        self._evidence[evidence.id] = evidence
        return True

    def export(self, session_id: SessionId) -> tuple[Evidence, ...]:
        return tuple(
            sorted(
                (item for item in self._evidence.values() if item.session_id == session_id),
                key=lambda item: (item.observed_at, str(item.id)),
            )
        )

    def replace(self, session_id: SessionId, evidence: tuple[Evidence, ...]) -> None:
        self._evidence = {
            item.id: item for item in self._evidence.values() if item.session_id != session_id
        }
        for item in evidence:
            self._evidence[item.id] = item

    def query(self, query: EvidenceQuery) -> tuple[Evidence, ...]:
        matches = [
            item
            for item in self._evidence.values()
            if item.session_id == query.session_id
            and (query.task_id is None or item.task_id == query.task_id)
            and (query.subject is None or item.subject == query.subject)
            and (query.claim is None or item.claim == query.claim)
        ]
        matches.sort(key=lambda item: (item.observed_at, str(item.id)), reverse=True)
        if query.limit is not None:
            matches = matches[: query.limit]
        return tuple(matches)
