from __future__ import annotations

from collections.abc import Iterable

from universal_agent.core import SessionId
from universal_agent.evidence import Evidence
from universal_agent.world.models import WorldFact, WorldModel, WorldSnapshot, WorldUpdater


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

    def forget(self, session_id: SessionId) -> None:
        self._facts = {key: value for key, value in self._facts.items() if key[0] != session_id}

    def rebuild(
        self,
        session_id: SessionId,
        evidence: Iterable[Evidence],
        updaters: tuple[WorldUpdater, ...],
    ) -> None:
        if not updaters:
            raise ValueError("world rebuild requires at least one updater")
        self.forget(session_id)
        ordered = sorted(
            (item for item in evidence if item.session_id == session_id),
            key=lambda item: (item.observed_at, str(item.id)),
        )
        for item in ordered:
            for updater in updaters:
                updater.apply(self, item)

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
