from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent.core import ErrorCode, ExecutionStatus
from universal_agent.evaluation.scenario_builder import ScenarioBuilder, load_canonical_scenarios
from universal_agent.evaluation.scenario_config import load_evaluation_suite_config

SCENARIOS_DIR = Path(__file__).parent.parent / "scenarios"


@pytest.mark.unit
def test_scenario_builder_creates_valid_scenario() -> None:
    config = (
        ScenarioBuilder("test-scenario")
        .description("A test scenario")
        .goal("Test health", [("healthy", True)])
        .task("Check health", ["healthy"])
        .expectations(
            status=ExecutionStatus.COMPLETED,
            capabilities=["inspect_workload"],
            evidence=["deployment.status"],
        )
        .tags(["test", "kubernetes"])
        .quality_gate(0.9, 0.95)
        .build_suite_config()
    )
    assert config.suite.name == "test-scenario"
    assert len(config.suite.scenarios) == 1
    scenario = config.suite.scenarios[0]
    assert scenario.goal.description == "Test health"
    assert scenario.task.description == "Check health"
    assert scenario.expectations.required_capabilities == ("inspect_workload",)
    assert config.quality_gate is not None
    assert config.quality_gate.min_pass_rate == 0.9


@pytest.mark.unit
def test_scenario_builder_with_policy_expectations() -> None:
    config = (
        ScenarioBuilder("policy-test")
        .goal("Test policy", [("blocked", True)])
        .task("Try dangerous op", ["blocked"])
        .expectations(
            status=ExecutionStatus.FAILED,
            error_code=ErrorCode.POLICY_DENIED,
        )
        .kind("policy")
        .build_suite_config()
    )
    scenario = config.suite.scenarios[0]
    assert scenario.expectations.expected_status == ExecutionStatus.FAILED
    assert scenario.expectations.expected_error_code == ErrorCode.POLICY_DENIED


@pytest.mark.unit
def test_load_canonical_scenarios() -> None:
    if not SCENARIOS_DIR.exists():
        pytest.skip("scenarios directory not found")
    configs = load_canonical_scenarios(SCENARIOS_DIR)
    assert len(configs) >= 4
    all_names = []
    for config in configs:
        for scenario in config.suite.scenarios:
            all_names.append(scenario.name)
    assert "find-and-fix-under-replicated-deployment" in all_names
    assert "diagnose-crashloopbackoff-pod" in all_names
    assert "detect-and-mitigate-memory-pressure" in all_names
    assert "policy-blocks-production-delete" in all_names


@pytest.mark.unit
def test_canonical_scenarios_have_quality_gates() -> None:
    if not SCENARIOS_DIR.exists():
        pytest.skip("scenarios directory not found")
    configs = load_canonical_scenarios(SCENARIOS_DIR)
    for config in configs:
        quality_gate = config.quality_gate
        assert quality_gate is not None, f"{config.suite.name} missing quality gate"
        assert quality_gate.min_goal_completion_rate is not None
        assert quality_gate.min_pass_rate > 0
        assert quality_gate.min_goal_completion_rate > 0


@pytest.mark.unit
def test_canonical_scenarios_load_individually() -> None:
    if not SCENARIOS_DIR.exists():
        pytest.skip("scenarios directory not found")
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        config = load_evaluation_suite_config(path)
        assert config.suite.name
        assert len(config.suite.scenarios) >= 1
