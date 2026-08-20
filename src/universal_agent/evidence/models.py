from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import NewType
from uuid import uuid4

from universal_agent.core import (
    ActionId,
    JsonValue,
    Observation,
    ObservationId,
    SessionId,
    Task,
    TaskId,
    utc_now,
)

EvidenceId = NewType("EvidenceId", str)


def new_evidence_id() -> EvidenceId:
    return EvidenceId(f"evidence-{uuid4()}")


@dataclass(frozen=True, slots=True)
class Evidence:
    session_id: SessionId
    task_id: TaskId
    action_id: ActionId
    observation_id: ObservationId
    subject: str
    claim: str
    value: JsonValue
    source: str
    confidence: float = 1.0
    id: EvidenceId = field(default_factory=new_evidence_id)
    observed_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("evidence confidence must be between zero and one")
        if not self.subject or not self.claim:
            raise ValueError("evidence subject and claim are required")


@dataclass(frozen=True, slots=True)
class EvidenceQuery:
    session_id: SessionId
    task_id: TaskId | None = None
    subject: str | None = None
    claim: str | None = None
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    session_id: SessionId
    task: Task
    observation: Observation


class EvidenceExtractor:
    @property
    def name(self) -> str: ...

    def extract(self, context: EvidenceContext) -> tuple[Evidence, ...]: ...


class StructuredEvidenceExtractor:
    name = "structured-observation"

    def extract(self, context: EvidenceContext) -> tuple[Evidence, ...]:
        if context.observation.status.value != "succeeded":
            return ()
        return tuple(
            Evidence(
                context.session_id,
                context.task.id,
                context.observation.action_id,
                context.observation.id,
                context.observation.source,
                key,
                value,
                context.observation.source,
                observed_at=context.observation.observed_at,
            )
            for key, value in context.observation.data.items()
        )
