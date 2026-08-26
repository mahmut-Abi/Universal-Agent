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
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.tools import UncertainToolExecutionError


class RemediationBackend:
    def __init__(
        self,
        *,
        verification_healthy: bool = True,
        mutation_timeout: bool = False,
        mutation_uncertain: bool = False,
        initial_inspection_timeout: bool = False,
    ) -> None:
        self.verification_healthy = verification_healthy
        self.mutation_timeout = mutation_timeout
        self.mutation_uncertain = mutation_uncertain
        self.initial_inspection_timeout = initial_inspection_timeout
        self.inspect_calls: list[str] = []
        self.mutation_calls = 0
        self.mutation_arguments: list[JsonMapping] = []
        self._scaled = False
        self._initial_timeout_used = False

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls.append(capability)
        if (
            capability == "inspect_workload"
            and self.initial_inspection_timeout
            and not self._initial_timeout_used
        ):
            self._initial_timeout_used = True
            raise TimeoutError("initial inspection timed out")
        if capability == "inspect_workload":
            if not self._scaled:
                return immutable_json(
                    {
                        "resource": "deployment/example",
                        "healthy": False,
                        "desired_replicas": 3,
                        "ready_replicas": 1,
                        "resource_version": "rv-before",
                    }
                )
            return immutable_json(
                {
                    "resource": "deployment/example",
                    "healthy": self.verification_healthy,
                    "desired_replicas": 3,
                    "ready_replicas": 3 if self.verification_healthy else 1,
                    "verification_observed": True,
                }
            )
        if capability == "inspect_pod":
            return immutable_json(
                {
                    "resource": "pod/example-123",
                    "root_cause": "under_replicated",
                }
            )
        if capability == "inspect_events":
            return immutable_json(
                {
                    "resource": "deployment/example",
                    "post_remediation_root_cause": "still_under_replicated",
                }
            )
        raise AssertionError(f"unexpected inspection capability: {capability}")

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        self.mutation_calls += 1
        self.mutation_arguments.append(immutable_json(arguments))
        if self.mutation_timeout:
            raise TimeoutError("scale mutation timed out")
        if self.mutation_uncertain:
            raise UncertainToolExecutionError("network closed after scale dispatch")
        self._scaled = True
        return immutable_json(
            {
                "resource": "deployment/example",
                "mutation_applied": True,
                "previous_replicas": 1,
                "replicas": 3,
                "mutation_id": "mutation-1",
            }
        )


def inspect_decision(capability: str, *observations: str) -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        f"Run {capability}",
        capability=capability,
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=observations,
    )


def scale_decision(
    *,
    replicas: int = 3,
    name: str = "example",
    namespace: str = "default",
) -> Decision:
    target = name if "/" in name else f"deployment/{name}"
    workload_name = name.split("/", 1)[1] if "/" in name else name
    return Decision(
        DecisionType.EXECUTE,
        "Scale the under-replicated workload",
        capability="scale_workload",
        target=target,
        arguments=immutable_json(
            {"name": workload_name, "namespace": namespace, "replicas": replicas}
        ),
        expected_observations=("mutation_applied",),
    )


def goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Restore workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ()),
    )


def scoped_goal_task() -> tuple[Goal, Task]:
    return (
        Goal(
            "Restore scoped workload health",
            (
                SuccessCriterion("healthy", True),
                SuccessCriterion("resource", "deployment/example"),
            ),
        ),
        Task("Inspect scoped workload", ("healthy", "resource")),
    )


def build_runtime(
    backend: RemediationBackend,
    decisions: list[Decision],
    state_store: InMemoryStateStore | None = None,
    *,
    environment: str = "staging",
) -> tuple[AgentRuntime, InMemoryStateStore, InMemoryEventSink]:
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    events = InMemoryEventSink()
    store = state_store or InMemoryStateStore()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": environment}),
    )
    return runtime, store, events


@pytest.mark.asyncio
async def test_remediation_completes_only_after_fresh_verification() -> None:
    backend = RemediationBackend()
    runtime, store, events = build_runtime(
        backend,
        [
            inspect_decision("inspect_workload", "healthy"),
            inspect_decision("inspect_pod", "root_cause"),
            scale_decision(),
            inspect_decision("inspect_workload", "verification_observed", "healthy"),
            Decision(DecisionType.FINISH, "Health verified after remediation"),
        ],
    )

    result = await runtime.run(*goal_task())
    snapshot = await store.load_session(result.session_id)
    event_types = [event.type for event in events.events]

    assert result.status is ExecutionStatus.COMPLETED
    assert backend.inspect_calls == ["inspect_workload", "inspect_pod", "inspect_workload"]
    assert backend.mutation_calls == 1
    assert backend.mutation_arguments[0]["current_replicas"] == 3
    assert backend.mutation_arguments[0]["resource_version"] == "rv-before"
    assert [node.key for node in snapshot.task_graph.nodes] == [
        "root",
        "diagnose-unhealthy-workload",
        "remediate-unhealthy-workload",
        "verify-remediation",
    ]
    enriched = next(event for event in events.events if event.type == "ActionArgumentsEnriched")
    assert enriched.data["argument_names"] == ("current_replicas", "resource_version")
    assert event_types[-1] == "GoalCompleted"
    assert "PolicyChecked" in event_types


@pytest.mark.asyncio
async def test_inspection_timeout_recovers_but_mutation_is_not_retried() -> None:
    backend = RemediationBackend(initial_inspection_timeout=True)
    runtime, _, events = build_runtime(
        backend,
        [
            inspect_decision("inspect_workload", "healthy"),
            inspect_decision("inspect_pod", "root_cause"),
            scale_decision(),
            inspect_decision("inspect_workload", "verification_observed", "healthy"),
            Decision(DecisionType.FINISH, "Health verified after remediation"),
        ],
    )

    result = await runtime.run(*goal_task())
    event_types = [event.type for event in events.events]

    assert result.status is ExecutionStatus.COMPLETED
    assert backend.inspect_calls == [
        "inspect_workload",
        "inspect_workload",
        "inspect_pod",
        "inspect_workload",
    ]
    assert backend.mutation_calls == 1
    assert "RecoveryPlanned" in event_types


@pytest.mark.asyncio
async def test_mutation_timeout_stops_without_retry() -> None:
    backend = RemediationBackend(mutation_timeout=True)
    runtime, _, events = build_runtime(
        backend,
        [
            inspect_decision("inspect_workload", "healthy"),
            inspect_decision("inspect_pod", "root_cause"),
            scale_decision(),
        ],
    )

    result = await runtime.run(*goal_task())
    event_types = [event.type for event in events.events]

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code is ErrorCode.TIMEOUT
    assert backend.mutation_calls == 1
    assert event_types.count("ActionStarted") == 3


@pytest.mark.asyncio
async def test_uncertain_mutation_stops_without_retrying_side_effect() -> None:
    backend = RemediationBackend(mutation_uncertain=True)
    runtime, _, events = build_runtime(
        backend,
        [
            inspect_decision("inspect_workload", "healthy"),
            inspect_decision("inspect_pod", "root_cause"),
            scale_decision(),
        ],
    )

    result = await runtime.run(*goal_task())
    action_completed = [event for event in events.events if event.type == "ActionCompleted"]

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code is ErrorCode.UNKNOWN_EXECUTION
    assert backend.mutation_calls == 1
    assert action_completed[-1].data["status"] == "unknown"
    assert action_completed[-1].data["error_code"] == "unknown_execution"


@pytest.mark.asyncio
async def test_invalid_scale_is_denied_before_mutation() -> None:
    backend = RemediationBackend()
    runtime, _, events = build_runtime(
        backend,
        [
            inspect_decision("inspect_workload", "healthy"),
            inspect_decision("inspect_pod", "root_cause"),
            scale_decision(replicas=0),
        ],
    )

    result = await runtime.run(*goal_task())
    event_types = [event.type for event in events.events]

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code is ErrorCode.POLICY_DENIED
    assert backend.mutation_calls == 0
    assert event_types.count("ActionStarted") == 2


@pytest.mark.asyncio
async def test_scale_outside_goal_resource_scope_is_denied_before_mutation() -> None:
    backend = RemediationBackend()
    runtime, _, events = build_runtime(
        backend,
        [
            inspect_decision("inspect_workload", "healthy", "resource"),
            inspect_decision("inspect_pod", "root_cause"),
            scale_decision(name="other"),
        ],
    )

    result = await runtime.run(*scoped_goal_task())
    event_types = [event.type for event in events.events]

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code is ErrorCode.POLICY_DENIED
    assert backend.mutation_calls == 0
    assert event_types.count("ActionStarted") == 2
    assert "GoalCompleted" not in event_types


@pytest.mark.asyncio
async def test_production_scale_pauses_and_rebuilt_runtime_resumes() -> None:
    backend = RemediationBackend()
    state_store = InMemoryStateStore()
    first, _, first_events = build_runtime(
        backend,
        [
            inspect_decision("inspect_workload", "healthy"),
            inspect_decision("inspect_pod", "root_cause"),
            scale_decision(),
        ],
        state_store,
        environment="production",
    )

    waiting = await first.run(*goal_task())
    paused = await state_store.load_session(waiting.session_id)

    assert waiting.status is ExecutionStatus.WAITING
    assert paused.state.pending_action is not None
    assert backend.mutation_calls == 0
    assert [event.type for event in first_events.events].count("PolicyChecked") == 3

    second, _, second_events = build_runtime(
        backend,
        [
            inspect_decision("inspect_workload", "verification_observed", "healthy"),
            Decision(DecisionType.FINISH, "Health verified after remediation"),
        ],
        state_store,
        environment="production",
    )
    completed = await second.resume(waiting.session_id, confirmed=True)

    assert completed.status is ExecutionStatus.COMPLETED
    assert backend.mutation_calls == 1
    assert [event.type for event in second_events.events].count("PolicyChecked") == 2


@pytest.mark.asyncio
async def test_failed_verification_expands_post_remediation_diagnosis() -> None:
    backend = RemediationBackend(verification_healthy=False)
    runtime, store, events = build_runtime(
        backend,
        [
            inspect_decision("inspect_workload", "healthy"),
            inspect_decision("inspect_pod", "root_cause"),
            scale_decision(),
            inspect_decision("inspect_workload", "verification_observed", "healthy"),
            inspect_decision("inspect_events", "post_remediation_root_cause"),
            Decision(DecisionType.FINISH, "Attempt to finish before health is verified"),
        ],
    )

    result = await runtime.run(*goal_task())
    snapshot = await store.load_session(result.session_id)
    event_types = [event.type for event in events.events]

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code is ErrorCode.INVALID_STATE
    assert "diagnose-after-remediation" in [node.key for node in snapshot.task_graph.nodes]
    assert "GoalCompleted" not in event_types
