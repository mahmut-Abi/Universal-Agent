from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from universal_agent import (
    AgentExpectedOutput,
    AgentRuntime,
    AgentTaskRequest,
    AgentTaskResultStatus,
    Decision,
    DecisionType,
    DomainLoader,
    Goal,
    InMemoryEventSink,
    InMemoryStateStore,
    ModelUsage,
    RuntimeAgentExecutor,
    RuntimeAPI,
    RuntimeBuilder,
    ScriptedModelAdapter,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core import (
    ErrorCode,
    EventId,
    ExecutionStatus,
    GoalId,
    GoalStatus,
    JsonMapping,
    RuntimeEvent,
    SessionId,
    TaskId,
    TaskStatus,
)
from universal_agent.domains.kubernetes import KubernetesDomain, KubernetesRemediationDomain
from universal_agent.runtime import RuntimeEventView, event_view


class HealthBackend:
    def __init__(self) -> None:
        self.calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.calls += 1
        assert capability == "inspect_workload"
        return immutable_json({"resource": "deployment/example", "healthy": True})


class RemediationBackend:
    def __init__(self) -> None:
        self.inspect_calls: list[str] = []
        self.mutation_calls = 0
        self._scaled = False

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls.append(capability)
        if capability == "inspect_workload":
            return immutable_json(
                {
                    "resource": "deployment/example",
                    "healthy": self._scaled,
                    "desired_replicas": 3,
                    "ready_replicas": 3 if self._scaled else 1,
                    "verification_observed": self._scaled,
                }
            )
        if capability == "inspect_pod":
            return immutable_json(
                {
                    "resource": "pod/example-123",
                    "root_cause": "under_replicated",
                }
            )
        raise AssertionError(f"unexpected capability: {capability}")

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        self.mutation_calls += 1
        self._scaled = True
        return immutable_json(
            {
                "resource": "deployment/example",
                "mutation_applied": True,
                "replicas": 3,
            }
        )


def inspect_workload(*expected: str) -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=expected,
    )


def inspect_pod() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect pod",
        capability="inspect_pod",
        target="pod/example-123",
        arguments=immutable_json({"name": "example-123"}),
        expected_observations=("root_cause",),
    )


def scale_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Scale workload",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 3}),
        expected_observations=("mutation_applied",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Required evidence is present")


def wait() -> Decision:
    return Decision(DecisionType.WAIT, "Operator pause requested")


def health_goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )


def remediation_goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Restore workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ()),
    )


def build_health_api(
    decisions: list[Decision],
    *,
    usage: tuple[ModelUsage, ...] = (),
) -> tuple[RuntimeAPI, InMemoryStateStore, InMemoryEventSink, HealthBackend]:
    backend = HealthBackend()
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions, usage=usage),
        state_store=store,
        components=RuntimeBuilder().build(DomainLoader().load(KubernetesDomain(backend))),
        event_sink=events,
    )
    return (
        RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        store,
        events,
        backend,
    )


def build_remediation_api(
    backend: RemediationBackend,
    decisions: list[Decision],
    store: InMemoryStateStore,
    events: InMemoryEventSink,
) -> RuntimeAPI:
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=store,
        components=RuntimeBuilder().build(
            DomainLoader().load(KubernetesRemediationDomain(backend, backend))
        ),
        event_sink=events,
        environment=immutable_json({"environment": "production"}),
    )
    return RuntimeAPI(runtime=runtime, session_store=store, event_reader=events)


@pytest.mark.contract
def test_runtime_event_view_deep_copies_nested_json_data() -> None:
    nested_items = [1]
    nested = {"items": nested_items}
    event = RuntimeEvent(
        "NestedEvent",
        SessionId("session-1"),
        GoalId("goal-1"),
        TaskId("task-1"),
        data={"nested": nested},
    )

    view = event_view(event)
    nested_items.append(2)

    assert view.data["nested"] == {"items": [1]}
    with pytest.raises(TypeError):
        cast(dict[str, object], view.data)["changed"] = True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runtime_agent_executor_reports_usage_from_runtime_events() -> None:
    api, _, _, _ = build_health_api(
        [inspect_workload("healthy"), finish()],
        usage=(
            ModelUsage(
                "scripted",
                "child-runtime",
                input_tokens=20,
                output_tokens=5,
                estimated_cost_micros=30,
            ),
            ModelUsage(
                "scripted",
                "child-runtime",
                input_tokens=10,
                output_tokens=2,
                estimated_cost_micros=12,
            ),
        ),
    )

    result = await RuntimeAgentExecutor(api).execute_agent_task(
        AgentTaskRequest(
            goal="Verify delegated workload health",
            expected_output=AgentExpectedOutput(
                "health_report",
                immutable_json({"success_criteria": [{"key": "healthy", "expected": True}]}),
            ),
        )
    )

    assert result.status is AgentTaskResultStatus.COMPLETED
    assert result.usage.model_call_count == 2
    assert result.usage.input_tokens == 30
    assert result.usage.output_tokens == 7
    assert result.usage.total_tokens == 37
    assert result.usage.estimated_cost == 0.000042
    assert result.usage.currency == "USD"


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_runtime_api_runs_goal_and_returns_immutable_session_projection() -> None:
    api, store, _, backend = build_health_api([inspect_workload("healthy"), finish()])

    run = await api.run_goal(*health_goal_task())
    loaded = await api.get_session(run.result.session_id)
    events = await api.list_events(run.result.session_id)

    assert run.result.status is ExecutionStatus.COMPLETED
    assert loaded.session_id == run.session.session_id
    assert loaded.goal_status is GoalStatus.COMPLETED
    assert loaded.current_task_status is TaskStatus.COMPLETED
    assert loaded.latest_evaluation is not None
    assert loaded.latest_evaluation.goal_completed
    assert loaded.domain_name == "kubernetes"
    assert loaded.domain_version == "0.1.0"
    assert [event.type for event in events][-1] == "GoalCompleted"
    assert all(event.session_id == run.result.session_id for event in events)
    assert backend.calls == 1

    snapshot = await store.load_session(run.result.session_id)
    snapshot.state.goal.status = GoalStatus.FAILED
    reloaded = await api.get_session(run.result.session_id)
    assert reloaded.goal_status is GoalStatus.COMPLETED

    with pytest.raises(TypeError):
        cast(dict[str, object], loaded.satisfied_criteria)["healthy"] = False
    with pytest.raises(TypeError):
        cast(dict[str, object], events[0].data)["changed"] = True


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_runtime_api_runs_compiled_goal_task_graph() -> None:
    api, _, _, backend = build_health_api(
        [
            inspect_workload("healthy"),
            inspect_workload("healthy"),
            finish(),
        ]
    )
    goal = Goal("Verify workload health", (SuccessCriterion("healthy", True),))

    run = await api.run_compiled_goal(goal)

    assert run.result.status is ExecutionStatus.COMPLETED
    assert len(run.session.tasks) == 2
    assert run.session.tasks[0].task_id == TaskId(f"goal:{goal.id}:root")
    assert [task.description for task in run.session.tasks] == [
        "Verify workload health",
        "Verify workload health",
    ]
    assert backend.calls == 2


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_runtime_api_returns_session_diagnostics_with_evidence() -> None:
    api, _, _, backend = build_health_api([inspect_workload("healthy"), finish()])

    run = await api.run_goal(*health_goal_task())
    diagnostics = await api.get_session_diagnostics(run.result.session_id)

    assert diagnostics.session.session_id == run.result.session_id
    assert diagnostics.session.goal_status is GoalStatus.COMPLETED
    claims = {item.claim: item for item in diagnostics.evidence}
    assert claims["healthy"].value is True
    assert claims["resource"].value == "deployment/example"
    assert claims["healthy"].source == "inspect_workload:kubernetes_inspect_workload"
    assert claims["healthy"].confidence == 0.99
    assert backend.calls == 1


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_runtime_api_lists_sessions_as_recent_summaries() -> None:
    api, store, _, backend = build_health_api(
        [
            inspect_workload("healthy"),
            finish(),
            inspect_workload("healthy"),
            finish(),
        ]
    )

    first = await api.run_goal(
        Goal(
            "Verify older workload",
            (SuccessCriterion("healthy", True),),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        Task("Inspect older workload", ("healthy",)),
    )
    second = await api.run_goal(
        Goal(
            "Verify newer workload",
            (SuccessCriterion("healthy", True),),
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        ),
        Task("Inspect newer workload", ("healthy",)),
    )

    summaries = await api.list_sessions()

    assert [item.session_id for item in summaries] == [
        second.result.session_id,
        first.result.session_id,
    ]
    assert summaries[0].goal_description == "Verify newer workload"
    assert summaries[0].goal_status is GoalStatus.COMPLETED
    assert summaries[0].current_task_status is TaskStatus.COMPLETED
    assert summaries[0].task_count == 1
    assert not summaries[0].pending_action
    assert summaries[0].domain_name == "kubernetes"
    assert summaries[0].created_at == datetime(2026, 1, 2, tzinfo=UTC)
    assert backend.calls == 2

    snapshot = await store.load_session(second.result.session_id)
    snapshot.state.goal.status = GoalStatus.FAILED
    reloaded = await api.list_sessions()
    assert reloaded[0].goal_status is GoalStatus.COMPLETED


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_runtime_api_streams_session_summaries_with_cursor_and_limit() -> None:
    api, _, _, _ = build_health_api(
        [
            inspect_workload("healthy"),
            finish(),
            inspect_workload("healthy"),
            finish(),
            inspect_workload("healthy"),
            finish(),
        ]
    )

    await api.run_goal(
        Goal(
            "Verify oldest workload",
            (SuccessCriterion("healthy", True),),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        Task("Inspect oldest workload", ("healthy",)),
    )
    await api.run_goal(
        Goal(
            "Verify middle workload",
            (SuccessCriterion("healthy", True),),
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        ),
        Task("Inspect middle workload", ("healthy",)),
    )
    await api.run_goal(
        Goal(
            "Verify newest workload",
            (SuccessCriterion("healthy", True),),
            created_at=datetime(2026, 1, 3, tzinfo=UTC),
        ),
        Task("Inspect newest workload", ("healthy",)),
    )

    first_batch = await api.stream_sessions(limit=2)
    second_batch = await api.stream_sessions(
        after_session_id=first_batch.sessions[-1].session_id,
        limit=2,
    )

    assert [item.goal_description for item in first_batch.sessions] == [
        "Verify newest workload",
        "Verify middle workload",
    ]
    assert first_batch.next_cursor == str(first_batch.sessions[-1].session_id)
    assert [item.goal_description for item in second_batch.sessions] == ["Verify oldest workload"]
    assert second_batch.next_cursor == str(second_batch.sessions[-1].session_id)

    with pytest.raises(ValueError, match="session list limit must be positive"):
        await api.stream_sessions(limit=0)

    with pytest.raises(ValueError, match="session cursor not found"):
        await api.stream_sessions(after_session_id=SessionId("missing-session"))


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_runtime_api_resumes_confirmation_and_reads_combined_events() -> None:
    backend = RemediationBackend()
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    first = build_remediation_api(
        backend,
        [inspect_workload("healthy"), inspect_pod(), scale_workload()],
        store,
        events,
    )

    waiting = await first.run_goal(*remediation_goal_task())

    assert waiting.result.status is ExecutionStatus.WAITING
    assert waiting.session.pending_action is not None
    assert waiting.session.pending_action.capability == "scale_workload"
    assert waiting.session.pending_action.attempt == 1
    assert len(waiting.session.pending_action.parameters_hash) == 64
    assert waiting.session.pending_action.idempotency_key.endswith(
        waiting.session.pending_action.parameters_hash[:16]
    )
    assert backend.mutation_calls == 0

    second = build_remediation_api(
        backend,
        [inspect_workload("verification_observed", "healthy"), finish()],
        store,
        events,
    )
    completed = await second.resume_session(waiting.result.session_id, confirmed=True)
    combined_events = await second.list_events(waiting.result.session_id)

    assert completed.result.status is ExecutionStatus.COMPLETED
    assert completed.session.goal_status is GoalStatus.COMPLETED
    assert completed.session.pending_action is None
    assert backend.mutation_calls == 1
    assert [event.type for event in combined_events].count("PolicyChecked") == 5
    assert [event.type for event in combined_events][-1] == "GoalCompleted"
    assert all(isinstance(event, RuntimeEventView) for event in combined_events)


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_runtime_api_pauses_and_resumes_waiting_session_without_pending_action() -> None:
    api, _, _, backend = build_health_api([wait(), inspect_workload("healthy"), finish()])

    waiting = await api.run_goal(*health_goal_task())
    paused = await api.pause_session(waiting.result.session_id, reason="operator paused session")
    resumed = await api.resume_session(waiting.result.session_id)
    events = await api.list_events(waiting.result.session_id)
    batch = await api.stream_events(
        waiting.result.session_id,
        after_event_id=EventId(events[0].event_id),
        limit=3,
    )

    assert waiting.result.status is ExecutionStatus.WAITING
    assert waiting.session.pending_action is None
    assert paused.result.status is ExecutionStatus.WAITING
    assert paused.session.goal_status is GoalStatus.WAITING
    assert paused.session.termination_reason == "operator paused session"
    assert resumed.result.status is ExecutionStatus.COMPLETED
    assert resumed.session.goal_status is GoalStatus.COMPLETED
    assert resumed.session.pending_action is None
    assert backend.calls == 1
    assert "SessionPaused" in [event.type for event in events]
    assert "SessionResumed" in [event.type for event in events]
    assert batch.next_cursor == batch.events[-1].event_id
    assert tuple(event.type for event in batch.events) == tuple(event.type for event in events[1:4])


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_runtime_api_cancels_waiting_confirmation_without_executing_action() -> None:
    backend = RemediationBackend()
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    api = build_remediation_api(
        backend,
        [inspect_workload("healthy"), inspect_pod(), scale_workload()],
        store,
        events,
    )
    waiting = await api.run_goal(*remediation_goal_task())

    cancelled = await api.cancel_session(
        waiting.result.session_id,
        reason="operator cancelled the session",
    )
    combined_events = await api.list_events(waiting.result.session_id)

    assert waiting.result.status is ExecutionStatus.WAITING
    assert cancelled.result.status is ExecutionStatus.CANCELLED
    assert cancelled.result.error_code is None
    assert cancelled.result.reason == "operator cancelled the session"
    assert cancelled.session.goal_status is GoalStatus.CANCELLED
    assert cancelled.session.current_task_status is TaskStatus.CANCELLED
    assert cancelled.session.pending_action is None
    assert backend.mutation_calls == 0
    assert [event.type for event in combined_events][-1] == "GoalCancelled"


@pytest.mark.asyncio
@pytest.mark.behavior
async def test_runtime_api_rejects_cancel_for_terminal_session_without_mutating_it() -> None:
    api, _, _, _ = build_health_api([inspect_workload("healthy"), finish()])
    completed = await api.run_goal(*health_goal_task())

    rejected = await api.cancel_session(completed.result.session_id)
    events = await api.list_events(completed.result.session_id)

    assert completed.result.status is ExecutionStatus.COMPLETED
    assert rejected.result.status is ExecutionStatus.FAILED
    assert rejected.result.error_code is ErrorCode.INVALID_STATE
    assert rejected.session.goal_status is GoalStatus.COMPLETED
    assert rejected.session.current_task_status is TaskStatus.COMPLETED
    assert [event.type for event in events][-1] == "GoalCompleted"
