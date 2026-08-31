from __future__ import annotations

from dataclasses import dataclass, field

from universal_agent.core import JsonMapping, JsonValue, immutable_json


@dataclass(frozen=True, slots=True)
class WorldStateSeed:
    """A fact to seed into the world model before running a scenario."""

    subject: str
    claim: str
    value: JsonValue
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class WorldEntitySeed:
    """An entity to seed into the world model before running a scenario."""

    entity_id: str
    kind: str
    attributes: JsonMapping = field(default_factory=lambda: immutable_json())


@dataclass(frozen=True, slots=True)
class EvaluationInitialState:
    """Pre-populate world/evidence state before running an evaluation scenario."""

    world_facts: tuple[WorldStateSeed, ...] = ()
    world_entities: tuple[WorldEntitySeed, ...] = ()


def build_initial_state_payload(
    initial_state: EvaluationInitialState | None,
) -> JsonMapping | None:
    """Convert EvaluationInitialState to a JsonMapping for run_goal."""
    if initial_state is None:
        return None
    world_facts: list[JsonValue] = []
    for fact in initial_state.world_facts:
        world_facts.append(
            {
                "subject": fact.subject,
                "claim": fact.claim,
                "value": fact.value,
                "confidence": fact.confidence,
            }
        )
    world_entities: list[JsonValue] = []
    for entity in initial_state.world_entities:
        world_entities.append(
            {
                "entity_id": entity.entity_id,
                "kind": entity.kind,
                "attributes": dict(entity.attributes) if entity.attributes else {},
            }
        )
    return immutable_json(
        {
            "world_facts": world_facts,
            "world_entities": world_entities,
        }
    )


_build_initial_state_payload = build_initial_state_payload
