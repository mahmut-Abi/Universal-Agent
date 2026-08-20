from __future__ import annotations

from universal_agent.core import SessionId
from universal_agent.evidence import Evidence
from universal_agent.world.models import WorldFact, WorldModel, WorldSnapshot


class FactWorldUpdater:
    name = "facts"

    def apply(self, model: WorldModel, evidence: Evidence) -> bool:
        return model.apply_fact(evidence)


class InMemoryWorldModel:
    def __init__(self) -> None:
        self._facts: dict[tuple[SessionId, str, str], list[Evidence]] = {}

    def apply_fact(self, evidence: Evidence) -> bool:
        key = (evidence.session_id, evidence.subject, evidence.claim)
        values = self._facts.setdefault(key, [])
        if any(item.id == evidence.id for item in values):
            return False
        values.append(evidence)
        return True

    def snapshot(
        self,
        session_id: SessionId,
        *,
        subjects: tuple[str, ...] = (),
        claims: tuple[str, ...] = (),
    ) -> WorldSnapshot:
        facts: list[WorldFact] = []
        for (stored_session, subject, claim), evidence in self._facts.items():
            if stored_session != session_id:
                continue
            if subjects and subject not in subjects:
                continue
            if claims and claim not in claims:
                continue
            current = max(
                evidence,
                key=lambda item: (item.confidence, item.observed_at, str(item.id)),
            )
            provenance = tuple(
                item.id
                for item in sorted(
                    evidence,
                    key=lambda item: (item.observed_at, str(item.id)),
                )
            )
            facts.append(
                WorldFact(
                    subject,
                    claim,
                    current.value,
                    current.confidence,
                    current.observed_at,
                    provenance,
                )
            )
        facts.sort(key=lambda item: (item.subject, item.claim))
        return WorldSnapshot(session_id, tuple(facts))
