from __future__ import annotations

import pytest

from universal_agent import (
    AgentProfile,
    AgentRuntime,
    Decision,
    DecisionType,
    DomainConfig,
    DomainLoader,
    InMemoryEventSink,
    InMemoryStateStore,
    RuntimeAPI,
    RuntimeBuilder,
    RuntimeConfig,
    RuntimeSDKError,
    RuntimeService,
    ScriptedModelAdapter,
    SDKGoal,
    SDKSuccessCriterion,
    SDKTask,
    UniversalAgentRuntime,
    immutable_json,
)
from universal_agent.core import JsonMapping
from universal_agent.domains.kubernetes import KubernetesRemediationDomain


class SDKBackend:
    def __init__(self) -> None:
        self.inspect_calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls += 1
        assert capability == "inspect_workload"
        return immutable_json(
            {
                "resource": "deployment/example",
                "healthy": True,
                "kind": "Deployment",
            }
        )

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        raise AssertionError(f"unexpected mutation: {capability}")


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload through SDK",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "SDK goal complete")


def wait() -> Decision:
    return Decision(DecisionType.WAIT, "SDK goal waiting for operator")


def build_sdk(decisions: list[Decision]) -> tuple[UniversalAgentRuntime, SDKBackend]:
    backend = SDKBackend()
    active = DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    components = RuntimeBuilder().build(active)
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "sdk-test"}),
    )
    service = RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
        profiles=(production_profile(),),
    )
    return UniversalAgentRuntime(service, default_profile="production-operator"), backend


def production_profile() -> AgentProfile:
    domain = DomainConfig("kubernetes", "0.2.0")
    return AgentProfile(
        "production-operator",
        "1.0.0",
        "Production Kubernetes operator",
        domain,
        RuntimeConfig(domain=domain),
        (domain,),
    )


@pytest.mark.asyncio
async def test_runtime_sdk_submits_goal_and_reads_events_through_service() -> None:
    sdk, backend = build_sdk([inspect_workload(), finish()])

    result = await sdk.submit_goal(
        "Verify workload health",
        success_criteria={"healthy": True},
        task="Inspect workload",
    )
    session = await sdk.get_session(result.session_id)
    events = await sdk.stream_events(result.session_id, limit=10)

    assert result.status == "completed"
    assert result.goal_status == "completed"
    assert result.current_task_status == "completed"
    assert result.reason == "workload health criteria satisfied"
    assert backend.inspect_calls == 1
    assert session.goal_description == "Verify workload health"
    assert session.tasks[0].required_criteria == ("healthy",)
    assert events.events


@pytest.mark.asyncio
async def test_runtime_sdk_accepts_public_goal_and_task_types() -> None:
    sdk, _ = build_sdk([inspect_workload(), finish()])
    goal = SDKGoal(
        "Verify workload health",
        (SDKSuccessCriterion("healthy", True),),
    )
    task = SDKTask("Inspect workload with explicit criteria", ("healthy",))

    result = await sdk.submit_goal(goal, task=task)
    session = await sdk.get_session(result.session_id)

    assert result.status == "completed"
    assert session.current_task_description == "Inspect workload with explicit criteria"


@pytest.mark.asyncio
async def test_runtime_sdk_validates_profile_selection() -> None:
    sdk, _ = build_sdk([finish()])

    with pytest.raises(RuntimeSDKError, match="unknown profile: missing"):
        await sdk.submit_goal(
            "Verify workload health",
            success_criteria={"healthy": True},
            profile="missing",
        )


@pytest.mark.asyncio
async def test_runtime_sdk_lifecycle_methods_return_public_results() -> None:
    sdk, _ = build_sdk([wait()])

    waiting = await sdk.submit_goal("Verify workload health", success_criteria={"healthy": True})
    paused = await sdk.pause_session(waiting.session_id, reason="operator pause")
    cancelled = await sdk.cancel_session(paused.session_id, reason="operator cancelled")

    assert waiting.status == "waiting"
    assert paused.status == "waiting"
    assert cancelled.status == "cancelled"
    assert cancelled.goal_status == "cancelled"


def test_runtime_sdk_rejects_invalid_public_inputs() -> None:
    with pytest.raises(RuntimeSDKError, match="success criteria must not be empty"):
        SDKGoal("No criteria", ())

    with pytest.raises(RuntimeSDKError, match="task description must not be empty"):
        SDKTask("", ())
