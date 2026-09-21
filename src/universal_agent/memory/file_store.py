"""Durable file-backed MemoryStore for the Golden Path (UA-LIVE-2026-09-21 Q6).

One JSON document per line under the configured store directory, guarded by a
file lock so concurrent agentd processes do not clobber each other. This is a
local durability adapter — not a database abstraction; SQLite/Postgres memory
persistence remains future work.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import jsonlines
from filelock import FileLock

from universal_agent.core import JsonCodecError, SessionId, immutable_json, utc_now
from universal_agent.memory.models import (
    MemoryId,
    MemoryKind,
    MemoryQuery,
    MemoryRecord,
)

_MEMORY_FILENAME = "memories.jsonl"
_MEMORY_LOCK_FILENAME = "memories.lock"


def _encode_record(record: MemoryRecord) -> dict[str, object]:
    return {
        "id": str(record.id),
        "kind": record.kind.value,
        "subject": record.subject,
        "content": record.content,
        "scope": record.scope,
        "confidence": record.confidence,
        "source_session_id": (
            None if record.source_session_id is None else str(record.source_session_id)
        ),
        "created_at": record.created_at.isoformat(),
        "version": record.version,
        "tags": list(record.tags),
        "source": record.source,
        "metadata": dict(record.metadata),
    }


def _decode_record(payload: dict[str, object]) -> MemoryRecord:
    created_at = payload.get("created_at")
    parsed_created_at = (
        datetime.fromisoformat(str(created_at)) if isinstance(created_at, str) else utc_now()
    )
    source_session = payload.get("source_session_id")
    kind = payload.get("kind")
    tags = payload.get("tags")
    return MemoryRecord(
        kind=MemoryKind(str(kind)) if isinstance(kind, str) else MemoryKind.SEMANTIC,
        subject=str(payload.get("subject", "")),
        content=str(payload.get("content", "")),
        scope=str(payload.get("scope", "")),
        confidence=float(payload.get("confidence", 1.0)),  # type: ignore[arg-type]
        source_session_id=SessionId(str(source_session)) if source_session is not None else None,
        id=MemoryId(str(payload.get("id", ""))),
        created_at=parsed_created_at,
        version=int(payload.get("version", 1)),  # type: ignore[call-overload]
        tags=tuple(str(item) for item in tags) if isinstance(tags, list) else (),
        source=str(payload.get("source", "")),
        metadata=immutable_json(
            dict(payload["metadata"]) if isinstance(payload["metadata"], dict) else {}
        ),
    )


class FileMemoryStore:
    """MemoryStore protocol implementation persisted to a JSONL file."""

    def __init__(self, root: str | Path, *, filename: str = _MEMORY_FILENAME) -> None:
        self._path = Path(root) / filename
        self._lock = FileLock(str(Path(root) / _MEMORY_LOCK_FILENAME))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.touch()

    def _load(self) -> dict[MemoryId, MemoryRecord]:
        records: dict[MemoryId, MemoryRecord] = {}
        with jsonlines.open(self._path, mode="r") as reader:
            for payload in reader:
                try:
                    record = _decode_record(dict(payload))
                except (JsonCodecError, ValueError, KeyError, TypeError):
                    continue  # skip corrupt lines rather than losing the store
                records[record.id] = record
        return records

    def _save(self, records: dict[MemoryId, MemoryRecord]) -> None:
        ordered = sorted(records.values(), key=lambda item: (item.created_at, str(item.id)))
        with jsonlines.open(self._path, mode="w") as writer:
            for record in ordered:
                writer.write(_encode_record(record))

    def add(self, record: MemoryRecord) -> bool:
        with self._lock:
            records = self._load()
            if record.id in records:
                return False
            records[record.id] = record
            self._save(records)
            return True

    def get(self, memory_id: MemoryId) -> MemoryRecord | None:
        with self._lock:
            return self._load().get(memory_id)

    def delete(self, memory_id: MemoryId) -> bool:
        with self._lock:
            records = self._load()
            if memory_id not in records:
                return False
            del records[memory_id]
            self._save(records)
            return True

    def export(self) -> tuple[MemoryRecord, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._load().values(),
                    key=lambda item: (item.created_at, str(item.id)),
                )
            )

    def query(self, query: MemoryQuery) -> tuple[MemoryRecord, ...]:
        records = self.export()
        matches = [
            record
            for record in records
            if not query.kinds or record.kind in query.kinds
            if not query.subjects or record.subject in query.subjects
            if query.scope is None or record.scope == query.scope or record.scope == ""
        ]
        matches.sort(
            key=lambda item: (item.created_at, str(item.id)),
            reverse=query.limit is not None,
        )
        if query.limit is not None:
            matches = matches[: query.limit]
        return tuple(matches)
