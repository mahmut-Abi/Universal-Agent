from __future__ import annotations

import pytest

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DomainLoader,
    Goal,
    InMemoryEventSink,
    InMemoryStateStore,
    RuntimeBuilder,
    ScriptedModelAdapter,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core import ErrorCode, ExecutionStatus, GoalStatus, TaskStatus
from universal_agent.domains.kubernetes import KubernetesDomain


class FakeKubernetesBackend:
    def __init__(self, observations: list[bool]) -> None:
        self._observations = iter(observations)
        self.calls = 0

    async def inspect(self, capability, arguments):  # type: ignore[no-untyped-def]
        self.calls += 1
        return immutable_json({"healthy": next(self._observations)})


def execute_probe() -> Decision:
    return Decision(
        type=DecisionType.EXECUTE,
        reason="Observe current workload health",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def finish() -> Decision:
    return Decision(type=DecisionType.FINISH, reason="Required health evidence is present")


def build_runtime(decisions, observations, *, max_iterations=10):  # type: ignore[no-untyped-def]
    backend = FakeKubernetesBackend(observations)
    active = DomainLoader().load(KubernetesDomain(backend))
    components = RuntimeBuilder().build(active)
    model = ScriptedModelAdapter(decisions)
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=model,
        state_store=store,
        components=components,
        event_sink=events,
        max_iterations=max_iterations,
    )
    return runtime, model, store, events, backend


def health_goal_and_task() -> tuple[Goal, Task]:
    return (
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )


@pytest.mark.asyncio
async def test_normal_loop_requires_evaluator_before_finish() -> None:
    runtime, model, store, events, backend = build_runtime(
        [execute_probe(), execute_probe(), finish()],
        [False, True],
    )
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)
    state = await store.load(result.session_id)

    assert result.status is ExecutionStatus.COMPLETED
    assert result.iterations == 3
    assert backend.calls == 2
    assert state.goal.status is GoalStatus.COMPLETED
    assert state.current_task.status is TaskStatus.COMPLETED
    assert model.contexts[0].capabilities[0].name == "inspect_cluster"
    assert not hasattr(model.contexts[0], "tools")
    event_types = [event.type for event in events.events]
    assert event_types.count("EvaluationCompleted") == 2
    assert event_types[-1] == "GoalCompleted"
    assert all(event.session_id == result.session_id for event in events.events)


@pytest.mark.asyncio
async def test_finish_is_rejected_without_evaluation() -> None:
    runtime, _, store, events, _ = build_runtime([finish()], [])
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)
    state = await store.load(result.session_id)

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code is ErrorCode.INVALID_STATE
    assert state.goal.status is GoalStatus.FAILED
    assert events.events[-1].type == "GoalFailed"


@pytest.mark.asyncio
async def test_unknown_capability_fails_before_action() -> None:
    decision = Decision(
        DecisionType.EXECUTE,
        "Inspect",
        capability="missing",
        expected_observations=("healthy",),
    )
    runtime, _, _, events, backend = build_runtime([decision], [])
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)

    assert result.error_code is ErrorCode.UNKNOWN_CAPABILITY
    assert backend.calls == 0
    assert not any(event.type == "ActionStarted" for event in events.events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "message"),
    [
        (Decision(DecisionType.WAIT, "External work is pending"), None),
        (
            Decision(
                DecisionType.ASK_USER,
                "A required value is missing",
                message="Which target should be inspected?",
            ),
            "Which target should be inspected?",
        ),
    ],
)
async def test_wait_and_ask_user_pause_runtime(decision, message):  # type: ignore[no-untyped-def]
    runtime, _, store, _, _ = build_runtime([decision], [])
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)
    state = await store.load(result.session_id)
    assert result.status is ExecutionStatus.WAITING
    assert result.user_message == message
    assert state.goal.status is GoalStatus.WAITING
    assert state.current_task.status is TaskStatus.WAITING


@pytest.mark.asyncio
async def test_iteration_limit_is_runtime_owned() -> None:
    runtime, _, _, _, _ = build_runtime(
        [execute_probe(), execute_probe()],
        [False, False],
        max_iterations=2,
    )
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)
    assert result.error_code is ErrorCode.ITERATION_LIMIT
    assert result.iterations == 2


@pytest.mark.asyncio
async def test_invalid_decision_is_rejected_before_resolution() -> None:
    invalid = Decision(DecisionType.EXECUTE, "Inspect")
    runtime, _, _, events, _ = build_runtime([invalid], [])
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)
    assert result.error_code is ErrorCode.VALIDATION_ERROR
    assert not any(event.type == "CapabilityResolved" for event in events.events)
