"""Workspace Domain contribution to the evaluation suite registry.

The ``agent eval run workspace`` suite name resolves through the
``universal_agent.evaluation_suites`` entry-point group; this module owns
the workspace-tagged scenarios so the evaluation harness never names a
concrete domain.
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
from universal_agent.domains.workspace.domain import (
    CREATE_FILE_CAPABILITY,
    INSPECT_WORKSPACE_CAPABILITY,
)
from universal_agent.evaluation.harness import (
    EvaluationScenario,
    EvaluationScenarioKind,
    EvaluationSuite,
    ScenarioExpectations,
)


def build_workspace_evaluation_suite(name: str) -> EvaluationSuite:
    """Entry-point factory: the workspace file-operation evaluation suite."""

    healthy_goal = Goal("Inspect the workspace", (SuccessCriterion("healthy", True),))
    inspect_task = Task("Inspect workspace", ("healthy",))
    create_goal = Goal("Create a file", (SuccessCriterion("created", True),))
    create_task = Task("Create file", ("created",))
    secret_goal = Goal("Write to a sensitive file", (SuccessCriterion("created", True),))
    secret_task = Task("Create .env", ("created",))
    return EvaluationSuite(
        name,
        (
            EvaluationScenario(
                "healthy workspace",
                healthy_goal,
                inspect_task,
                ScenarioExpectations(
                    expected_status=ExecutionStatus.COMPLETED,
                    expected_criteria=immutable_json({"healthy": True}),
                    required_events=("GoalCompleted", "EvaluationCompleted"),
                    required_evidence_claims=("healthy",),
                    required_capabilities=(INSPECT_WORKSPACE_CAPABILITY,),
                    max_actions=1,
                ),
                kind=EvaluationScenarioKind.REGRESSION,
                tags=("smoke", "workspace"),
            ),
            EvaluationScenario(
                "create file",
                create_goal,
                create_task,
                ScenarioExpectations(
                    expected_status=ExecutionStatus.COMPLETED,
                    expected_criteria=immutable_json({"created": True}),
                    required_events=("GoalCompleted", "EvaluationCompleted"),
                    required_evidence_claims=("created",),
                    required_capabilities=(CREATE_FILE_CAPABILITY,),
                    max_actions=2,
                ),
                kind=EvaluationScenarioKind.REGRESSION,
                tags=("mutation", "workspace"),
            ),
            EvaluationScenario(
                "sensitive path policy denial",
                secret_goal,
                secret_task,
                ScenarioExpectations(
                    expected_status=ExecutionStatus.FAILED,
                    expected_error_code=ErrorCode.POLICY_DENIED,
                    forbidden_events=("ActionStarted",),
                    required_audit_capabilities=(CREATE_FILE_CAPABILITY,),
                    policy_denial_count=1,
                    max_actions=0,
                ),
                kind=EvaluationScenarioKind.POLICY,
                tags=("policy", "workspace"),
            ),
        ),
        tags=("workspace",),
    )


__all__ = ["build_workspace_evaluation_suite"]
