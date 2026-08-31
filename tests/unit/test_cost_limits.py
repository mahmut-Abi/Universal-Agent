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
from universal_agent.core import ErrorCode, ExecutionStatus, JsonMapping
from universal_agent.domains.kubernetes import KubernetesDomain
from universal_agent.model.adapter import ModelUsage


class FakeKubernetesBackend:
    def __init__(self, observations: list[bool]) -> None:
        self._observations = iter(observations)
        self.calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.calls += 1
        return immutable_json({"healthy": next(self._observations)})


def execute_probe() -> Decision:
    return Decision(
        type=DecisionType.EXECUTE,
        reason="inspect workload",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def finish() -> Decision:
    return Decision(type=DecisionType.FINISH, reason="done")


def health_goal_and_task() -> tuple[Goal, Task]:
    return (
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )


def _usage(cost: int = 100, tokens: int = 10) -> ModelUsage:
    return ModelUsage(
        provider="test",
        model="test-model",
        input_tokens=tokens,
        output_tokens=tokens,
        estimated_cost_micros=cost,
    )


def _build_runtime(
    decisions: list[Decision],
    *,
    usage: list[ModelUsage] | None = None,
    max_total_cost_micros: int | None = None,
    max_total_tokens: int | None = None,
) -> tuple[AgentRuntime, ScriptedModelAdapter, InMemoryStateStore, InMemoryEventSink]:
    backend = FakeKubernetesBackend([True])
    active = DomainLoader().load(KubernetesDomain(backend))
    components = RuntimeBuilder().build(active)
    if usage is not None:
        model = ScriptedModelAdapter(decisions, usage=usage)
    else:
        model = ScriptedModelAdapter(decisions)
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=model,
        state_store=store,
        components=components,
        event_sink=events,
        max_total_cost_micros=max_total_cost_micros,
        max_total_tokens=max_total_tokens,
    )
    return runtime, model, store, events


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cost_limit_halts_session() -> None:
    runtime, _model, _store, _events = _build_runtime(
        [execute_probe(), finish()],
        usage=[_usage(cost=500), _usage(cost=600)],
        max_total_cost_micros=1000,
    )
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)
    assert result.status == ExecutionStatus.FAILED
    assert result.error_code == ErrorCode.COST_LIMIT_EXCEEDED
    assert "cost limit reached" in result.reason


@pytest.mark.asyncio
@pytest.mark.unit
async def test_token_limit_halts_session() -> None:
    runtime, _model, _store, _events = _build_runtime(
        [execute_probe(), finish()],
        usage=[_usage(tokens=60), _usage(tokens=60)],
        max_total_tokens=100,
    )
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)
    assert result.status == ExecutionStatus.FAILED
    assert result.error_code == ErrorCode.COST_LIMIT_EXCEEDED
    assert "token limit reached" in result.reason


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cost_limit_not_exceeded_succeeds() -> None:
    runtime, _model, _store, _events = _build_runtime(
        [execute_probe(), finish()],
        usage=[_usage(cost=100), _usage(cost=100)],
        max_total_cost_micros=999999999,
    )
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)
    assert result.status == ExecutionStatus.COMPLETED


@pytest.mark.asyncio
@pytest.mark.unit
async def test_no_cost_limit_succeeds() -> None:
    runtime, _model, _store, _events = _build_runtime(
        [execute_probe(), finish()],
        usage=[_usage(cost=99999), _usage(cost=99999)],
    )
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)
    assert result.status == ExecutionStatus.COMPLETED


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cumulative_cost_tracked_in_events() -> None:
    runtime, _model, _store, events = _build_runtime(
        [execute_probe(), finish()],
        usage=[_usage(cost=100), _usage(cost=100)],
        max_total_cost_micros=999999999,
    )
    goal, task = health_goal_and_task()
    await runtime.run(goal, task)
    all_events = await events.list_events()
    event_types = [e.type for e in all_events]
    assert "ModelUsageRecorded" in event_types


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cost_limit_zero_halts_immediately() -> None:
    runtime, _model, _store, _events = _build_runtime(
        [execute_probe(), finish()],
        usage=[_usage(cost=1)],
        max_total_cost_micros=0,
    )
    goal, task = health_goal_and_task()
    result = await runtime.run(goal, task)
    assert result.status == ExecutionStatus.FAILED
    assert result.error_code == ErrorCode.COST_LIMIT_EXCEEDED
