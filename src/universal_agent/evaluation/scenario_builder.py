from __future__ import annotations

from pathlib import Path

from universal_agent.core import (
    ErrorCode,
    ExecutionStatus,
    Goal,
    JsonValue,
    SuccessCriterion,
    Task,
)
from universal_agent.evaluation.harness import (
    EvaluationQualityGate,
    EvaluationScenario,
    EvaluationScenarioKind,
    EvaluationSuite,
    ScenarioExpectations,
)
from universal_agent.evaluation.scenario_config import (
    EvaluationSuiteConfig,
    load_evaluation_suite_config,
)


class ScenarioBuilder:
    """Builder for constructing evaluation scenarios programmatically."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._description = ""
        self._goal_description = ""
        self._success_criteria: list[SuccessCriterion] = []
        self._task_description = ""
        self._required_criteria: list[str] = []
        self._expected_status = ExecutionStatus.COMPLETED
        self._max_iterations: int | None = None
        self._expected_error_code: ErrorCode | None = None
        self._required_capabilities: list[str] = []
        self._required_evidence_claims: list[str] = []
        self._required_events: list[str] = []
        self._kind = EvaluationScenarioKind.SCENARIO
        self._tags: list[str] = []
        self._min_pass_rate = 0.8
        self._min_goal_completion_rate = 0.85

    def description(self, value: str) -> ScenarioBuilder:
        self._description = value
        return self

    def goal(self, description: str, criteria: list[tuple[str, JsonValue]]) -> ScenarioBuilder:
        self._goal_description = description
        self._success_criteria = [SuccessCriterion(k, v) for k, v in criteria]
        return self

    def task(self, description: str, required: list[str]) -> ScenarioBuilder:
        self._task_description = description
        self._required_criteria = required
        return self

    def expectations(
        self,
        *,
        status: ExecutionStatus = ExecutionStatus.COMPLETED,
        max_iterations: int | None = None,
        error_code: ErrorCode | None = None,
        capabilities: list[str] | None = None,
        evidence: list[str] | None = None,
        events: list[str] | None = None,
    ) -> ScenarioBuilder:
        self._expected_status = status
        self._max_iterations = max_iterations
        self._expected_error_code = error_code
        self._required_capabilities = capabilities or []
        self._required_evidence_claims = evidence or []
        self._required_events = events or []
        return self

    def kind(self, value: EvaluationScenarioKind | str) -> ScenarioBuilder:
        self._kind = (
            value if isinstance(value, EvaluationScenarioKind) else EvaluationScenarioKind(value)
        )
        return self

    def tags(self, values: list[str]) -> ScenarioBuilder:
        self._tags = values
        return self

    def quality_gate(self, pass_rate: float, completion_rate: float) -> ScenarioBuilder:
        self._min_pass_rate = pass_rate
        self._min_goal_completion_rate = completion_rate
        return self

    def build_scenario(self) -> EvaluationScenario:
        goal = Goal(
            self._goal_description,
            tuple(self._success_criteria),
        )
        task = Task(self._task_description, tuple(self._required_criteria))
        expectations = ScenarioExpectations(
            expected_status=self._expected_status,
            expected_error_code=self._expected_error_code,
            max_iterations=self._max_iterations,
            required_capabilities=tuple(self._required_capabilities),
            required_evidence_claims=tuple(self._required_evidence_claims),
            required_events=tuple(self._required_events),
        )
        return EvaluationScenario(
            name=self._name,
            goal=goal,
            task=task,
            expectations=expectations,
            kind=self._kind,
            tags=tuple(self._tags),
        )

    def build_suite_config(self) -> EvaluationSuiteConfig:
        scenario = self.build_scenario()
        suite = EvaluationSuite(
            name=self._name,
            scenarios=(scenario,),
        )
        quality_gate = EvaluationQualityGate(
            min_pass_rate=self._min_pass_rate,
            min_goal_completion_rate=self._min_goal_completion_rate,
        )
        return EvaluationSuiteConfig(suite=suite, quality_gate=quality_gate)


def load_canonical_scenarios(directory: Path) -> list[EvaluationSuiteConfig]:
    """Load all canonical scenario JSON files from a directory."""
    configs: list[EvaluationSuiteConfig] = []
    for path in sorted(directory.glob("*.json")):
        configs.append(load_evaluation_suite_config(path))
    return configs
