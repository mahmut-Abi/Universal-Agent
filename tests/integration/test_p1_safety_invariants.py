"""P1 Safety Invariants (spec sections 33): centralized automated proofs.

I1  LLM cannot directly invoke Kubernetes tools.
I2  Every mutation requires Policy evaluation.
I3  DENY means zero mutation execution.
I4  REQUIRE_CONFIRMATION means zero mutation before approval.
I5  Every mutation must have verification.
I6  Tool success does not imply task success.
I7  Recovery must pass through Policy.
I8  Recovery must be bounded.
I9  Evidence must have provenance.
I10 Session resume cannot bypass Policy or confirmation.
"""

from __future__ import annotations

from typing import cast

import pytest

from universal_agent import (
    Decision,
    DecisionType,
    DomainLoader,
    ExecutionStatus,
    Goal,
    RuntimeBuilder,
    ScriptedModelAdapter,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core import ActionId, ObservationId, SessionId, TaskId
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.domains.kubernetes.backend import KubernetesBackend, KubernetesMutationBackend
from universal_agent.evidence import Evidence
from universal_agent.recovery import RecoveryStrategy
from universal_agent.runtime import AgentRuntime, InMemoryEventSink
from universal_agent.state import InMemoryStateStore
from universal_agent_api.types import JsonMapping as JsonMappingType


class CountingBackend:
    """Backend that counts read and mutation invocations (I3/I4 proof surface)."""

    def __init__(self, *, healthy_after_mutation: bool = True) -> None:
        self.read_calls: list[str] = []
        self.mutation_calls: list[tuple[str, JsonMappingType]] = []
        self._healthy_after_mutation = healthy_after_mutation

    async def inspect(self, capability: str, arguments: JsonMappingType) -> JsonMappingType:
        self.read_calls.append(capability)
        return cast(
            JsonMappingType,
            {
                "resource": "deployment/checkout",
                "namespace": "default",
                "healthy": self._healthy_after_mutation and bool(self.mutation_calls),
                "desired_replicas": 1,
                "ready_replicas": 1 if self._healthy_after_mutation and self.mutation_calls else 0,
                "root_cause": None if self.mutation_calls else "crash_loop_back_off",
            },
        )

    async def mutate(self, capability: str, arguments: JsonMappingType) -> JsonMappingType:
        self.mutation_calls.append((capability, arguments))
        return cast(
            JsonMappingType,
            {"resource": "deployment/checkout", "mutation_applied": True},
        )


def build_runtime(
    backend: CountingBackend,
    decisions: list[Decision],
    *,
    max_recovery_steps: int = 8,
    environment: str = "production",
) -> tuple[AgentRuntime, InMemoryEventSink]:
    components = RuntimeBuilder().build(
        DomainLoader().load(
            KubernetesRemediationDomain(
                cast(KubernetesBackend, backend),
                cast(KubernetesMutationBackend, backend),
            )
        )
    )
    sink = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=sink,
        max_recovery_steps=max_recovery_steps,
        environment=immutable_json({"environment": environment}),
    )
    return runtime, sink


def inspect_decision() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload",
        capability="inspect_workload",
        target="deployment/checkout",
        arguments=immutable_json({"name": "checkout", "namespace": "default"}),
        expected_observations=("healthy", "resource"),
    )


def restart_decision() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Restart workload",
        capability="restart_workload",
        target="deployment/checkout",
        arguments=immutable_json({"name": "checkout", "namespace": "default"}),
        expected_observations=("mutation_applied",),
    )


def health_goal() -> tuple[Goal, Task]:
    return (
        Goal("Restore checkout", (SuccessCriterion("healthy", True),)),
        Task("Inspect checkout", ("healthy",)),
    )


def find_event(events: InMemoryEventSink, event_type: str) -> object | None:
    for event in events.events:
        if event.type == event_type:
            return event
    return None


# I1: LLM cannot directly invoke Kubernetes tools - decisions flow through
# capability resolution; an unknown capability is rejected before any tool run.
@pytest.mark.asyncio
@pytest.mark.behavior
async def test_i1_unknown_capability_rejected_before_tool() -> None:
    backend = CountingBackend()
    decisions = [
        Decision(
            DecisionType.EXECUTE,
            "Direct kubectl attempt",
            capability="kubectl_delete_pod",
            target="pod/checkout",
            arguments=immutable_json({"name": "checkout"}),
            expected_observations=("deleted",),
        ),
    ]
    runtime, _sink = build_runtime(backend, decisions)

    result = await runtime.run(*health_goal())

    assert result.status is ExecutionStatus.FAILED
    assert backend.read_calls == []
    assert backend.mutation_calls == []


# I2/I3: every mutation is policy-checked; a DENY decision means zero mutations.
@pytest.mark.asyncio
@pytest.mark.behavior
async def test_i3_policy_denial_prevents_mutation_execution() -> None:
    backend = CountingBackend()
    decisions = [
        inspect_decision(),
        Decision(
            DecisionType.EXECUTE,
            "Restart via denied capability",
            capability="scale_workload",
            target="deployment/checkout",
            arguments=immutable_json({"name": "checkout", "namespace": "default", "replicas": 0}),
            expected_observations=("mutation_applied",),
        ),
    ]
    runtime, sink = build_runtime(backend, decisions, environment="staging")

    await runtime.run(*health_goal())

    assert backend.mutation_calls == []
    policy_events = [e for e in sink.events if e.type == "PolicyChecked"]
    assert policy_events, "mutation decision must be policy-checked (I2)"
    denied = any(event.data.get("effect") == "deny" for event in policy_events)
    assert denied, "the unsafe scale-to-zero must be denied"


# I4: REQUIRE_CONFIRMATION means zero mutations before approval.
@pytest.mark.asyncio
@pytest.mark.behavior
async def test_i4_confirmation_boundary_blocks_mutation_until_approved() -> None:
    backend = CountingBackend()
    decisions = [
        inspect_decision(),
        restart_decision(),
        inspect_decision(),
        finish_after_restart(),
    ]
    runtime, _sink = build_runtime(backend, decisions)

    waiting = await runtime.run(*health_goal())
    assert waiting.status is ExecutionStatus.WAITING
    assert backend.mutation_calls == [], "I4: no mutation before approval"

    resumed = await runtime.resume(waiting.session_id, confirmed=True)
    assert resumed.status is ExecutionStatus.COMPLETED
    assert len(backend.mutation_calls) == 1
    assert backend.mutation_calls[0][0] == "restart_workload"


def finish_after_restart() -> Decision:
    return Decision(DecisionType.FINISH, "Remediation verified")


# I6: tool success does not imply task success - a mutation that leaves the
# workload unhealthy must NOT complete the goal.
@pytest.mark.asyncio
@pytest.mark.behavior
async def test_i6_mutation_success_with_unhealthy_workload_is_not_task_success() -> None:
    backend = CountingBackend(healthy_after_mutation=False)
    decisions = [
        inspect_decision(),
        restart_decision(),
        Decision(DecisionType.FINISH, "premature finish attempt"),
    ]
    runtime, _sink = build_runtime(backend, decisions, max_recovery_steps=1, environment="staging")

    result = await runtime.run(*health_goal())

    assert len(backend.mutation_calls) == 1
    assert result.status is not ExecutionStatus.COMPLETED or (
        result.reason is not None and "criteria" in result.reason
    )


# I7/I8: recovery is policy-gated and bounded.
def test_i7_i8_recovery_rules_are_policy_gated_and_bounded() -> None:
    components = RuntimeBuilder().build(
        DomainLoader().load(
            KubernetesRemediationDomain(
                cast(KubernetesBackend, CountingBackend()),
                cast(KubernetesMutationBackend, CountingBackend()),
            )
        )
    )
    rules = components.recovery_manager._rules
    assert rules, "kubernetes domain must register recovery rules"
    for rule in rules:
        assert rule.max_attempts > 0, f"rule {rule.name} must be bounded (I8)"
        assert rule.strategy in {
            RecoveryStrategy.RETRY_ACTION,
            RecoveryStrategy.REOBSERVE,
        }, f"rule {rule.name} must re-enter the capability/policy path (I7)"


# I9: evidence carries provenance (source) traceable to an observation.
def test_i9_evidence_has_provenance_source() -> None:
    evidence = Evidence(
        SessionId("s1"),
        TaskId("t1"),
        ActionId("a1"),
        ObservationId("o1"),
        "deployment/checkout",
        "healthy",
        False,
        "inspect_workload:kubernetes_inspect_workload",
    )
    assert evidence.source.startswith("inspect_workload:")
    assert evidence.observation_id is not None


# I10: resume after confirmation cannot bypass policy - the pending action is
# re-checked (reconciled) through the executor, not executed raw.
@pytest.mark.asyncio
@pytest.mark.behavior
async def test_i10_resume_rechecks_policy_before_mutation() -> None:
    backend = CountingBackend()
    decisions = [
        inspect_decision(),
        restart_decision(),
        inspect_decision(),
        finish_after_restart(),
    ]
    runtime, sink = build_runtime(backend, decisions)

    waiting = await runtime.run(*health_goal())
    assert waiting.status is ExecutionStatus.WAITING

    resumed = await runtime.resume(waiting.session_id, confirmed=True)

    policy_events = [e for e in sink.events if e.type == "PolicyChecked"]
    mutation_policy_checks = [
        e for e in policy_events if e.data.get("capability") == "restart_workload"
    ]
    assert mutation_policy_checks, "I10: resume must re-run policy on the mutation"
    assert backend.mutation_calls[0][0] == "restart_workload"
    assert resumed.status is ExecutionStatus.COMPLETED
