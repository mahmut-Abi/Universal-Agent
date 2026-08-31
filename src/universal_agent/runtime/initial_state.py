from __future__ import annotations

from universal_agent.core import (
    ActionId,
    JsonMapping,
    ObservationId,
    TaskId,
    immutable_json,
)
from universal_agent.evidence import Evidence, EvidenceId
from universal_agent.runtime.session import SessionRuntimeState
from universal_agent.world.models import EntityId, WorldEntity


def seed_initial_state(
    session: SessionRuntimeState,
    initial_state: JsonMapping,
) -> None:
    """Seed session-local evidence and world state before execution."""
    session_id = session.state.session_id
    raw_facts = initial_state.get("world_facts")
    raw_entities = initial_state.get("world_entities")
    if not isinstance(raw_facts, list) or not isinstance(raw_entities, list):
        return

    for raw_fact in raw_facts:
        if not isinstance(raw_fact, dict):
            continue
        subject = str(raw_fact.get("subject", ""))
        claim = str(raw_fact.get("claim", ""))
        value = raw_fact.get("value")
        confidence = float(str(raw_fact.get("confidence", 1.0)))
        if not subject or not claim:
            continue
        evidence = Evidence(
            id=EvidenceId(f"init-{subject}-{claim}"),
            session_id=session_id,
            task_id=TaskId(""),
            action_id=ActionId(""),
            observation_id=ObservationId(""),
            subject=subject,
            claim=claim,
            value=value,
            confidence=confidence,
            source="initial_state",
        )
        session.evidence_store.add(evidence)
        session.world_model.apply_fact(evidence)

    for raw_entity in raw_entities:
        if not isinstance(raw_entity, dict):
            continue
        entity_id = str(raw_entity.get("entity_id", ""))
        kind = str(raw_entity.get("kind", ""))
        attributes = raw_entity.get("attributes")
        if not entity_id or not kind:
            continue
        attrs = immutable_json(attributes) if isinstance(attributes, dict) else immutable_json()
        world_entity = WorldEntity(EntityId(entity_id), kind, attrs)
        session.world_model.apply_entity(session_id, world_entity)
