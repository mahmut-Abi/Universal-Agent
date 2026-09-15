"""Kubernetes Domain contribution to the evaluation suite registry.

The ``agent eval run kubernetes`` suite name resolves through the
``universal_agent.evaluation_suites`` entry-point group; this module owns
the kubernetes-tagged workload-health scenarios so the evaluation harness
never names a concrete domain.
"""

from __future__ import annotations

from universal_agent.core import (
    ErrorCode,
    ExecutionStatus,
    Goal,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.evaluation.harness import (
    EvaluationScenario,
    EvaluationScenarioKind,
    EvaluationSuite,
    ScenarioExpectations,
)


def build_kubernetes_evaluation_suite(name: str) -> EvaluationSuite:
    """Entry-point factory: the kubernetes workload-health evaluation suite."""

    goal = Goal("Evaluate workload health", (SuccessCriterion("healthy", True),))
    task = Task("Inspect workload", ("healthy",))
    return EvaluationSuite(
        name,
        (
            EvaluationScenario(
                "healthy workload",
                goal,
                task,
                ScenarioExpectations(
                    expected_status=ExecutionStatus.COMPLETED,
                    expected_criteria=immutable_json({"healthy": True}),
                    required_events=("GoalCompleted", "EvaluationCompleted"),
                    required_evidence_claims=("healthy",),
                    required_capabilities=("inspect_workload",),
                    max_actions=1,
                ),
                kind=EvaluationScenarioKind.REGRESSION,
                tags=("smoke", "kubernetes"),
            ),
            EvaluationScenario(
                "invalid scale policy",
                goal,
                task,
                ScenarioExpectations(
                    expected_status=ExecutionStatus.FAILED,
                    expected_error_code=ErrorCode.POLICY_DENIED,
                    forbidden_events=("ActionStarted",),
                    required_audit_capabilities=("scale_workload",),
                    policy_denial_count=1,
                    max_actions=0,
                ),
                kind=EvaluationScenarioKind.POLICY,
                tags=("policy", "kubernetes"),
            ),
        ),
        tags=("kubernetes",),
    )


__all__ = ["build_kubernetes_evaluation_suite"]
