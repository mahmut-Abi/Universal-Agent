from __future__ import annotations

from datetime import UTC, datetime

from universal_agent.core import (
    JsonValue,
    Observation,
    ObservationStatus,
    SessionId,
    Task,
    immutable_json,
    new_action_id,
    new_observation_id,
)
from universal_agent.evidence import Evidence
from universal_agent.world import FactWorldUpdater, InMemoryWorldModel


def evidence(subject: str, claim: str, value: JsonValue) -> Evidence:
    task = Task("Project world model", ())
    action_id = new_action_id()
    observation = Observation(
        new_observation_id(),
        action_id,
        task.id,
        "example",
        ObservationStatus.SUCCEEDED,
        immutable_json({claim: value}),
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    return Evidence(
        SessionId("session-world"),
        task.id,
        action_id,
        observation.id,
        subject,
        claim,
        value,
        observation.source,
        observed_at=observation.observed_at,
    )


def main() -> None:
    model = InMemoryWorldModel()
    updater = FactWorldUpdater()
    for item in (
        evidence("deployment/example", "healthy", True),
        evidence("deployment/example", "kind", "Deployment"),
        evidence("deployment/example", "relation:owns", ["pod/example-1", "pod/example-2"]),
    ):
        updater.apply(model, item)

    snapshot = model.snapshot(SessionId("session-world"))
    entity = snapshot.entity_for("deployment/example")
    relation_targets = ",".join(
        str(item.target) for item in snapshot.relations_for(relation="owns")
    )

    print(
        f"facts={len(snapshot.facts)} entities={len(snapshot.entities)} "
        f"relations={len(snapshot.relations)}"
    )
    print(f"entity={entity.id if entity else ''} kind={entity.kind if entity else ''}")
    print(f"healthy={entity.attributes['healthy'] if entity else ''}")
    print(f"owns={relation_targets}")


if __name__ == "__main__":
    main()
