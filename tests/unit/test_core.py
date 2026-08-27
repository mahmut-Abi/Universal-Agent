from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from universal_agent.context import BasicContextCompiler
from universal_agent.core import (
    AgentState,
    CapabilityCategory,
    CapabilityDefinition,
    CapabilityInputContract,
    DateTimeParseError,
    Decision,
    DecisionType,
    Goal,
    SuccessCriterion,
    Task,
    immutable_json,
    new_session_id,
    parse_iso_datetime,
    runtime_primitives,
    utc_now,
    validate_argument_contract,
)
from universal_agent.memory import MemoryKind, MemoryRecord
from universal_agent.state import InMemoryStateStore, StateNotFoundError
from universal_agent.world import EntityId, WorldEntity, WorldFact, WorldRelation, WorldSnapshot


def test_decision_contract_rejects_invalid_shapes() -> None:
    with pytest.raises(ValueError, match="capability"):
        Decision(
            type=DecisionType.EXECUTE,
            reason="inspect",
            expected_observations=("healthy",),
        ).validate()
    with pytest.raises(ValueError, match="action"):
        Decision(
            type=DecisionType.FINISH,
            reason="done",
            capability="inspect_service",
        ).validate()
    with pytest.raises(ValueError, match="message"):
        Decision(type=DecisionType.ASK_USER, reason="need input").validate()


def test_runtime_primitives_override_ids_and_clock_inside_context() -> None:
    current = datetime(2026, 1, 1, tzinfo=UTC)
    counters: dict[str, int] = {}

    def clock() -> datetime:
        nonlocal current
        value = current
        current = current + timedelta(seconds=5)
        return value

    def id_factory(prefix: str) -> str:
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}-fixed-{counters[prefix]}"

    with runtime_primitives(clock=clock, id_factory=id_factory):
        goal = Goal("Verify service", (SuccessCriterion("healthy", True),))
        task = Task("Probe service", ("healthy",))

        assert goal.id == "goal-fixed-1"
        assert task.id == "task-fixed-1"
        assert goal.created_at == datetime(2026, 1, 1, tzinfo=UTC)
        assert task.created_at == datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC)
        assert new_session_id() == "session-fixed-1"
        assert utc_now() == datetime(2026, 1, 1, 0, 0, 10, tzinfo=UTC)

    assert new_session_id() != "session-fixed-2"


def test_parse_iso_datetime_uses_dateutil_and_timezone_policy() -> None:
    parsed = parse_iso_datetime(
        "2026-01-01T00:00:00Z",
        field="before",
        require_timezone=True,
    )

    assert parsed.isoformat() == "2026-01-01T00:00:00+00:00"
    with pytest.raises(DateTimeParseError, match="before must include a timezone"):
        parse_iso_datetime(
            "2026-01-01T00:00:00",
            field="before",
            require_timezone=True,
        )
    with pytest.raises(DateTimeParseError, match="created_at must be an ISO datetime string"):
        parse_iso_datetime("not-a-date", field="created_at")


def test_basic_context_exposes_capabilities_not_tools() -> None:
    state = AgentState(
        session_id=new_session_id(),
        goal=Goal("Verify service", (SuccessCriterion("healthy", True),)),
        current_task=Task("Probe service", ("healthy",)),
        satisfied_criteria={"healthy": False},
    )
    context = BasicContextCompiler().compile(
        state,
        (
            CapabilityDefinition(
                "inspect_service",
                "Inspect service",
                CapabilityCategory.OBSERVATION,
            ),
        ),
        ("read-only",),
        (),
        capability_input_contracts=(
            CapabilityInputContract(
                "inspect_service",
                required_arguments=("name",),
                argument_schema=immutable_json(
                    {
                        "required": ["name"],
                        "properties": {"name": {"type": "string", "minLength": 1}},
                    }
                ),
            ),
        ),
    )
    assert context.goal_id == state.goal.id
    assert context.satisfied_criteria == immutable_json({"healthy": False})
    assert context.capabilities[0].name == "inspect_service"
    assert context.capabilities[0].required_arguments == ("name",)
    assert context.capabilities[0].argument_schema["required"] == ["name"]
    assert context.goal_success_criteria == (SuccessCriterion("healthy", True),)
    assert context.current_task_required_criteria == ("healthy",)
    assert context.policy_summary == ("read-only",)


def test_argument_contract_uses_jsonschema_keywords_beyond_runtime_subset() -> None:
    schema = immutable_json(
        {
            "type": "object",
            "required": ["namespace"],
            "properties": {
                "namespace": {"type": "string", "pattern": "^[a-z0-9-]+$"},
                "selector": {
                    "oneOf": [
                        {"type": "string", "minLength": 1},
                        {"type": "null"},
                    ],
                },
            },
            "additionalProperties": False,
        }
    )

    accepted = validate_argument_contract(
        required_arguments=(),
        argument_schema=schema,
        arguments=immutable_json({"namespace": "prod-1", "selector": None}),
    )
    pattern_error = validate_argument_contract(
        required_arguments=(),
        argument_schema=schema,
        arguments=immutable_json({"namespace": "Prod!"}),
    )
    one_of_error = validate_argument_contract(
        required_arguments=(),
        argument_schema=schema,
        arguments=immutable_json({"namespace": "prod-1", "selector": ""}),
    )

    assert accepted is None
    assert pattern_error is not None
    assert "does not match" in pattern_error
    assert one_of_error is not None
    assert "not valid under any of the given schemas" in one_of_error


def test_basic_context_projects_world_entities_and_relations() -> None:
    observed_at = datetime(2026, 1, 1, tzinfo=UTC)
    state = AgentState(
        session_id=new_session_id(),
        goal=Goal("Verify service graph", (SuccessCriterion("healthy", True),)),
        current_task=Task("Probe service graph", ("healthy",)),
    )
    world = WorldSnapshot(
        state.session_id,
        facts=(WorldFact("deployment/example", "healthy", True, 0.9, observed_at, ()),),
        entities=(
            WorldEntity(
                EntityId("deployment/example"),
                "Deployment",
                immutable_json({"healthy": True}),
            ),
            WorldEntity(EntityId("pod/example-1"), "Pod"),
        ),
        relations=(
            WorldRelation(
                EntityId("deployment/example"),
                "owns",
                EntityId("pod/example-1"),
            ),
        ),
    )

    context = BasicContextCompiler().compile(state, (), (), (), world=world)
    fragments = {fragment.key: fragment for fragment in context.world_context}

    assert fragments["world.deployment/example.healthy"].priority == 20
    assert fragments["world.entity.deployment/example"].priority == 21
    assert fragments["world.relation.deployment/example.owns.pod/example-1"].priority == 22
    assert "Deployment" in fragments["world.entity.deployment/example"].content
    assert (
        "deployment/example -[owns]-> pod/example-1"
        in fragments["world.relation.deployment/example.owns.pod/example-1"].content
    )


def test_memory_context_uses_advisory_priority_and_independent_budget() -> None:
    state = AgentState(
        session_id=new_session_id(),
        goal=Goal("Verify service", (SuccessCriterion("healthy", True),)),
        current_task=Task("Probe service", ("healthy",)),
    )
    memories = tuple(
        MemoryRecord(MemoryKind.SEMANTIC, f"note-{index}", "content " * 30, confidence=0.9)
        for index in range(20)
    )
    compiler = BasicContextCompiler(max_memory_fragments=4, max_memory_characters=1_200)
    context = compiler.compile(
        state,
        (
            CapabilityDefinition(
                "inspect_service",
                "Inspect service",
                CapabilityCategory.OBSERVATION,
            ),
        ),
        ("read-only",),
        (),
        memories=memories,
    )
    assert len(context.memory_context) <= 4
    assert sum(len(fragment.content) for fragment in context.memory_context) <= 1_200
    # Priority 40: below task (10), world (20), evidence (30); advisory, dropped first.
    assert all(fragment.priority == 40 for fragment in context.memory_context)
    # Default-compiled context without memories carries no memory fragments.
    plain = BasicContextCompiler().compile(state, (), (), ())
    assert plain.memory_context == ()


@pytest.mark.asyncio
async def test_state_store_controls_session_lifecycle() -> None:
    store = InMemoryStateStore()
    state = AgentState(
        session_id=new_session_id(),
        goal=Goal("Verify", ()),
        current_task=Task("Inspect", ()),
    )
    await store.create(state)
    loaded = await store.load(state.session_id)
    assert loaded is not state
    assert loaded.session_id == state.session_id
    assert loaded.goal.id == state.goal.id
    assert loaded.current_task.id == state.current_task.id
    with pytest.raises(ValueError, match="already exists"):
        await store.create(state)
    with pytest.raises(StateNotFoundError):
        await store.load(new_session_id())
