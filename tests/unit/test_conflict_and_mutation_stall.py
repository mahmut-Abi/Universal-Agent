"""Tests for recoverable idempotency conflicts and mutation-loop advisories."""

from __future__ import annotations

from universal_agent.context.compiler import BasicContextCompiler
from universal_agent.core import (
    ActionId,
    AgentState,
    ErrorCode,
    Goal,
    GoalId,
    JsonMapping,
    JsonValue,
    ObservationId,
    SessionId,
    SuccessCriterion,
    Task,
    TaskId,
)
from universal_agent.domain import DomainLoader, RuntimeBuilder
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.evidence import Evidence, EvidenceId
from universal_agent.recovery import (
    FailureCategory,
    classify_failure,
)
from universal_agent.runtime.session import SessionRuntimeState, start_session


def test_idempotency_conflict_classifies_as_transient_not_unknown() -> None:
    category = classify_failure(ErrorCode.RESOURCE_CONFLICT)

    assert category is FailureCategory.TRANSIENT


def _runtime_session() -> SessionRuntimeState:

    class RecordingMutationBackend:
        async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
            return {"resource": "deployment/api", "healthy": False}

        async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
            return {"resource": "deployment/api", "mutation_applied": True}

    components = RuntimeBuilder().build(
        DomainLoader().load(
            KubernetesRemediationDomain(
                RecordingMutationBackend(),
                RecordingMutationBackend(),
            )
        )
    )
    state = AgentState(
        SessionId("session-conflict"),
        Goal("fix workload", (SuccessCriterion("healthy", True),), id=GoalId("goal-1")),
        Task("remediate", ("healthy",), id=TaskId("task-1")),
    )
    return start_session(state, components)


def make_evidence(
    *,
    source: str,
    claim: str,
    value: JsonValue,
    sequence: int,
    session_id: SessionId,
) -> Evidence:
    return Evidence(
        session_id,
        TaskId("task-1"),
        ActionId(f"action-{sequence}"),
        ObservationId(f"observation-{sequence}"),
        "deployment/api",
        claim,
        value,
        source,
        1.0,
        id=EvidenceId(f"ev-{sequence}"),
    )


def test_stall_advisory_triggers_for_repeated_mutation_without_health_change() -> None:
    compiler = BasicContextCompiler()
    session = _runtime_session()
    for index in range(1, 4):
        evidence = make_evidence(
            source="restart_workload:kubernetes_restart_workload",
            claim="healthy",
            value=False,
            sequence=index,
            session_id=session.state.session_id,
        )
        session.record(evidence)

    context = compiler.compile(
        session.state,
        (),
        (),
        (),
        evidence=session.query(),
    )

    stall = [f for f in context.domain_context if f.key == "runtime.stall_advisory"]
    assert len(stall) == 1
    assert "restart_workload" in stall[0].content
    assert "report" in stall[0].content.lower() or "finish" in stall[0].content.lower()


def test_stall_advisory_suppressed_when_mutation_restores_health() -> None:
    compiler = BasicContextCompiler()
    session = _runtime_session()
    # Mutation succeeded on the last round: health flipped, no stall.
    for index, healthy in enumerate((False, False, True), start=1):
        evidence = make_evidence(
            source="restart_workload:kubernetes_restart_workload",
            claim="healthy",
            value=healthy,
            sequence=index,
            session_id=session.state.session_id,
        )
        session.record(evidence)

    context = compiler.compile(
        session.state,
        (),
        (),
        (),
        evidence=session.query(),
    )

    stall = [f for f in context.domain_context if f.key == "runtime.stall_advisory"]
    assert not stall
