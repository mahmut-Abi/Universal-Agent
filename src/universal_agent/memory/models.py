from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import NewType
from uuid import uuid4

from universal_agent.core import SessionId, utc_now

MemoryId = NewType("MemoryId", str)


def new_memory_id() -> MemoryId:
    return MemoryId(f"memory-{uuid4()}")


class MemoryKind(StrEnum):
    """The four knowledge kinds from the design doc.

    Episodic is what actually happened in a past session and is written only by
    the Runtime at a terminal transition. Semantic / Procedural / Preference are
    generally-true prior knowledge a Domain may declare.
    """

    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    PREFERENCE = "preference"


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    kind: MemoryKind
    subject: str
    content: str
    scope: str = ""
    confidence: float = 1.0
    source_session_id: SessionId | None = None
    id: MemoryId = field(default_factory=new_memory_id)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("memory confidence must be between zero and one")
        if not self.subject or not self.content:
            raise ValueError("memory subject and content are required")


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    kinds: tuple[MemoryKind, ...] = ()
    subjects: tuple[str, ...] = ()
    scope: str | None = None
    limit: int | None = None
