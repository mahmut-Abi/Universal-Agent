"""Tests for the decision-stall advisory in the context compiler.

A stall is N+ consecutive repeated observations from the same capability that
produce no new distinct fact values. The compiler surfaces an advisory fragment
so the model is told to change approach instead of looping on the same
inspection.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from universal_agent.context.compiler import BasicContextCompiler
from universal_agent.core import (
    ActionId,
    AgentState,
    Goal,
    GoalId,
    JsonValue,
    ObservationId,
    SessionId,
    SuccessCriterion,
    Task,
    TaskId,
)
from universal_agent.evidence import Evidence, EvidenceId


def make_state() -> AgentState:
    return AgentState(
        SessionId("session-stall"),
        Goal(
            "fix the workload",
            (SuccessCriterion("healthy", True),),
            id=GoalId("goal-stall"),
        ),
        Task(
            "diagnose",
            ("healthy",),
            id=TaskId("task-stall"),
        ),
    )


def make_evidence(
    *,
    source: str,
    claim: str,
    value: JsonValue,
    sequence: int,
) -> Evidence:
    observed = datetime(2026, 9, 13, 10, 0, tzinfo=UTC) + timedelta(seconds=sequence)
    return Evidence(
        SessionId("session-stall"),
        TaskId("task-stall"),
        ActionId(f"action-{sequence}"),
        ObservationId(f"observation-{sequence}"),
        "deployment/api",
        claim,
        value,
        source,
        1.0,
        id=EvidenceId(f"ev-{sequence}"),
        observed_at=observed,
    )


def test_stall_advisory_appended_after_repeated_capability_without_new_facts() -> None:
    compiler = BasicContextCompiler()
    state = make_state()
    # Three rounds of the same inspection capability yielding the same fact.
    evidence = (
        make_evidence(
            source="inspect_workload:kubernetes_inspect_workload",
            claim="healthy",
            value=False,
            sequence=1,
        ),
        make_evidence(
            source="inspect_workload:kubernetes_inspect_workload",
            claim="healthy",
            value=False,
            sequence=2,
        ),
        make_evidence(
            source="inspect_workload:kubernetes_inspect_workload",
            claim="healthy",
            value=False,
            sequence=3,
        ),
    )

    context = compiler.compile(state, (), (), (), evidence=evidence)

    stall = [f for f in context.domain_context if f.key == "runtime.stall_advisory"]
    assert len(stall) == 1
    assert "inspect_workload" in stall[0].content
    assert "3" in stall[0].content
    assert "finish" in stall[0].content.lower() or "report" in stall[0].content.lower()


def test_no_stall_advisory_when_observations_vary() -> None:
    compiler = BasicContextCompiler()
    state = make_state()
    evidence = (
        make_evidence(
            source="inspect_workload:kubernetes_inspect_workload",
            claim="healthy",
            value=False,
            sequence=1,
        ),
        make_evidence(
            source="inspect_logs:kubernetes_inspect_logs",
            claim="log_tail",
            value="error",
            sequence=2,
        ),
        make_evidence(
            source="inspect_workload:kubernetes_inspect_workload",
            claim="replicas",
            value=1,
            sequence=3,
        ),
    )

    context = compiler.compile(state, (), (), (), evidence=evidence)

    stall = [f for f in context.domain_context if f.key == "runtime.stall_advisory"]
    assert not stall


def test_no_stall_advisory_when_facts_keep_changing() -> None:
    compiler = BasicContextCompiler()
    state = make_state()
    # Same capability but each round observes a distinct new fact.
    evidence = (
        make_evidence(
            source="inspect_workload:kubernetes_inspect_workload",
            claim="healthy",
            value=False,
            sequence=1,
        ),
        make_evidence(
            source="inspect_workload:kubernetes_inspect_workload",
            claim="replicas",
            value=0,
            sequence=2,
        ),
        make_evidence(
            source="inspect_workload:kubernetes_inspect_workload",
            claim="pod_restarts",
            value=8,
            sequence=3,
        ),
    )

    context = compiler.compile(state, (), (), (), evidence=evidence)

    stall = [f for f in context.domain_context if f.key == "runtime.stall_advisory"]
    assert not stall
