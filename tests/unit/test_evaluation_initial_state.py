from __future__ import annotations

from typing import cast

from universal_agent.core import (
    Goal,
    JsonValue,
    Task,
    immutable_json,
)
from universal_agent.evaluation.harness import (
    EvaluationInitialState,
    EvaluationScenario,
    WorldEntitySeed,
    WorldStateSeed,
    _build_initial_state_payload,
)


class TestEvaluationInitialState:
    def test_world_state_seed(self) -> None:
        seed = WorldStateSeed(
            subject="pod-1",
            claim="status",
            value="Running",
            confidence=0.9,
        )
        assert seed.subject == "pod-1"
        assert seed.claim == "status"
        assert seed.value == "Running"
        assert seed.confidence == 0.9

    def test_world_entity_seed(self) -> None:
        seed = WorldEntitySeed(
            entity_id="pod-1",
            kind="Pod",
            attributes=immutable_json({"name": "pod-1"}),
        )
        assert seed.entity_id == "pod-1"
        assert seed.kind == "Pod"
        assert seed.attributes["name"] == "pod-1"

    def test_initial_state_none(self) -> None:
        result = _build_initial_state_payload(None)
        assert result is None

    def test_initial_state_with_facts(self) -> None:
        state = EvaluationInitialState(
            world_facts=(
                WorldStateSeed("pod-1", "status", "Running"),
                WorldStateSeed("pod-1", "restart_count", 5),
            ),
        )
        result = _build_initial_state_payload(state)
        assert result is not None
        assert "world_facts" in result
        facts = cast(list[JsonValue], result["world_facts"])
        assert len(facts) == 2
        first = cast(dict[str, JsonValue], facts[0])
        assert first["subject"] == "pod-1"
        assert first["claim"] == "status"
        assert first["value"] == "Running"

    def test_initial_state_with_entities(self) -> None:
        state = EvaluationInitialState(
            world_entities=(WorldEntitySeed("pod-1", "Pod", immutable_json({"name": "pod-1"})),),
        )
        result = _build_initial_state_payload(state)
        assert result is not None
        assert "world_entities" in result
        entities = cast(list[JsonValue], result["world_entities"])
        assert len(entities) == 1
        entity = cast(dict[str, JsonValue], entities[0])
        assert entity["entity_id"] == "pod-1"
        assert entity["kind"] == "Pod"

    def test_scenario_with_initial_state(self) -> None:
        scenario = EvaluationScenario(
            name="test scenario",
            goal=Goal(description="test goal", success_criteria=()),
            task=Task("test task", ()),
            initial_state=EvaluationInitialState(
                world_facts=(WorldStateSeed("pod-1", "status", "Running"),),
            ),
        )
        assert scenario.initial_state is not None
        assert len(scenario.initial_state.world_facts) == 1

    def test_scenario_without_initial_state(self) -> None:
        scenario = EvaluationScenario(
            name="test scenario",
            goal=Goal(description="test goal", success_criteria=()),
            task=Task("test task", ()),
        )
        assert scenario.initial_state is None
