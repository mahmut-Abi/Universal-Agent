from __future__ import annotations

from typing import Protocol

from universal_agent.memory.models import MemoryId, MemoryQuery, MemoryRecord


class MemoryStore(Protocol):
    def add(self, record: MemoryRecord) -> bool: ...

    def query(self, query: MemoryQuery) -> tuple[MemoryRecord, ...]: ...

    def export(self) -> tuple[MemoryRecord, ...]: ...


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._records: dict[MemoryId, MemoryRecord] = {}

    def add(self, record: MemoryRecord) -> bool:
        if record.id in self._records:
            return False
        self._records[record.id] = record
        return True

    def export(self) -> tuple[MemoryRecord, ...]:
        return tuple(
            sorted(
                self._records.values(),
                key=lambda item: (item.created_at, str(item.id)),
            )
        )

    def query(self, query: MemoryQuery) -> tuple[MemoryRecord, ...]:
        matches = [
            record
            for record in self._records.values()
            if not query.kinds or record.kind in query.kinds
            if not query.subjects or record.subject in query.subjects
            if query.scope is None or record.scope == query.scope or record.scope == ""
        ]
        matches.sort(key=lambda item: (item.created_at, str(item.id)))
        if query.limit is not None:
            matches = matches[: query.limit]
        return tuple(matches)
