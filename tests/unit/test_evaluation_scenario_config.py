from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent.core import ErrorCode, ExecutionStatus
from universal_agent.evaluation.harness import EvaluationScenarioKind
from universal_agent.evaluation.scenario_config import (
    evaluation_suite_config_from_mapping,
    evaluation_suite_from_mapping,
    load_evaluation_suite,
)


def test_evaluation_suite_config_parses_typed_scenarios() -> None:
    suite = evaluation_suite_from_mapping(
        {
            "name": "file suite",
            "tags": ["local", "kubernetes"],
            "scenarios": [
                {
                    "name": "healthy workload",
                    "kind": "regression",
                    "tags": ["smoke"],
                    "goal": {
                        "description": "Evaluate workload health",
                        "success_criteria": {"healthy": True},
                    },
                    "task": {
                        "description": "Inspect workload",
                        "required_criteria": ["healthy"],
                    },
                    "expectations": {
                        "expected_status": "completed",
                        "expected_criteria": {"healthy": True},
                        "required_events": ["GoalCompleted", "EvaluationCompleted"],
                        "required_evidence_claims": ["healthy"],
                        "required_capabilities": ["inspect_workload"],
                        "allowed_capabilities": ["inspect_workload"],
                        "max_actions": 1,
                        "max_iterations": 3,
                        "max_execution_duration_ms": 1000,
                        "max_model_total_tokens": 100,
                        "max_model_estimated_cost_micros": 10,
                    },
                },
                {
                    "name": "invalid scale policy",
                    "kind": "policy",
                    "goal": {
                        "description": "Evaluate policy denial",
                        "success_criteria": [
                            {"key": "healthy", "expected": True},
                        ],
                    },
                    "task": {
                        "description": "Attempt unsafe scale",
                        "required_criteria": ["healthy"],
                    },
                    "expectations": {
                        "expected_status": "failed",
                        "expected_error_code": "policy_denied",
                        "forbidden_events": ["ActionStarted"],
                        "required_audit_capabilities": ["scale_workload"],
                        "policy_denial_count": 1,
                        "resource_conflict_count": 0,
                        "active_resource_lock_count": 0,
                    },
                },
            ],
        }
    )

    healthy, policy = suite.scenarios

    assert suite.name == "file suite"
    assert suite.tags == ("local", "kubernetes")
    assert healthy.kind is EvaluationScenarioKind.REGRESSION
    assert healthy.tags == ("smoke",)
    assert healthy.goal.success_criteria[0].key == "healthy"
    assert healthy.goal.success_criteria[0].expected is True
    assert healthy.expectations.expected_status is ExecutionStatus.COMPLETED
    assert healthy.expectations.expected_criteria["healthy"] is True
    assert healthy.expectations.allowed_capabilities == ("inspect_workload",)
    assert healthy.expectations.max_execution_duration_ms == 1000
    assert healthy.expectations.max_model_total_tokens == 100
    assert policy.kind is EvaluationScenarioKind.POLICY
    assert policy.expectations.expected_status is ExecutionStatus.FAILED
    assert policy.expectations.expected_error_code is ErrorCode.POLICY_DENIED
    assert policy.expectations.required_audit_capabilities == ("scale_workload",)
    assert policy.expectations.policy_denial_count == 1


def test_evaluation_suite_config_parses_quality_gate() -> None:
    config = evaluation_suite_config_from_mapping(
        {
            "name": "gated suite",
            "quality_gate": {
                "min_pass_rate": 0.9,
                "min_goal_completion_rate": 1,
                "max_average_actions_per_scenario": 2,
                "max_total_model_estimated_cost_micros": 100,
            },
            "scenarios": [
                {
                    "name": "healthy workload",
                    "goal": {
                        "description": "Evaluate workload health",
                        "success_criteria": {"healthy": True},
                    },
                    "task": {
                        "description": "Inspect workload",
                        "required_criteria": ["healthy"],
                    },
                }
            ],
        }
    )

    assert config.suite.name == "gated suite"
    assert config.quality_gate is not None
    assert config.quality_gate.min_pass_rate == 0.9
    assert config.quality_gate.min_goal_completion_rate == 1.0
    assert config.quality_gate.max_average_actions_per_scenario == 2.0
    assert config.quality_gate.max_total_model_estimated_cost_micros == 100


def test_load_evaluation_suite_reads_json_file(tmp_path: Path) -> None:
    path = tmp_path / "suite.json"
    path.write_text(
        """
        {
          "name": "json suite",
          "scenarios": [
            {
              "name": "healthy workload",
              "goal": {
                "description": "Evaluate workload health",
                "success_criteria": {"healthy": true}
              },
              "task": {
                "description": "Inspect workload",
                "required_criteria": ["healthy"]
              }
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    suite = load_evaluation_suite(path)

    assert suite.name == "json suite"
    assert suite.scenarios[0].expectations.expected_status is ExecutionStatus.COMPLETED


def test_evaluation_suite_config_rejects_invalid_shapes() -> None:
    with pytest.raises(ValueError, match="scenarios is required"):
        evaluation_suite_from_mapping({"name": "broken"})

    with pytest.raises(ValueError, match=r"task\.required_criteria must be a list"):
        evaluation_suite_from_mapping(
            {
                "name": "broken",
                "scenarios": [
                    {
                        "name": "bad task",
                        "goal": {
                            "description": "Evaluate workload health",
                            "success_criteria": {"healthy": True},
                        },
                        "task": {
                            "description": "Inspect workload",
                            "required_criteria": "healthy",
                        },
                    }
                ],
            }
        )

    with pytest.raises(ValueError, match="'unknown' is not a valid EvaluationScenarioKind"):
        evaluation_suite_from_mapping(
            {
                "name": "broken",
                "scenarios": [
                    {
                        "name": "bad kind",
                        "kind": "unknown",
                        "goal": {
                            "description": "Evaluate workload health",
                            "success_criteria": {"healthy": True},
                        },
                        "task": {
                            "description": "Inspect workload",
                            "required_criteria": ["healthy"],
                        },
                    }
                ],
            }
        )
