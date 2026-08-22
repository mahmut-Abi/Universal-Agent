from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from universal_agent.core import (
    ErrorCode,
    ExecutionResult,
    ExecutionStatus,
    Goal,
    JsonMapping,
    SessionId,
    Task,
    immutable_json,
)
from universal_agent.operations import AuditRecordView, RuntimeMetricsView, build_runtime_metrics
from universal_agent.runtime import RuntimeEventView, RuntimeRun, SessionSummaryView, SessionView


class EvaluationRuntime(Protocol):
    async def run_goal(self, goal: Goal, task: Task) -> RuntimeRun: ...

    async def list_sessions(self) -> tuple[SessionSummaryView, ...]: ...

    async def list_events(self, session_id: SessionId) -> tuple[RuntimeEventView, ...]: ...

    async def audit_records(
        self,
        session_id: SessionId | None = None,
    ) -> tuple[AuditRecordView, ...]: ...


@dataclass(frozen=True, slots=True)
class ScenarioExpectations:
    expected_status: ExecutionStatus = ExecutionStatus.COMPLETED
    expected_error_code: ErrorCode | None = None
    expected_criteria: JsonMapping = field(default_factory=immutable_json)
    required_events: tuple[str, ...] = ()
    forbidden_events: tuple[str, ...] = ()
    required_evidence_claims: tuple[str, ...] = ()
    forbidden_evidence_claims: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    allowed_capabilities: tuple[str, ...] | None = None
    required_audit_capabilities: tuple[str, ...] = ()
    policy_denial_count: int | None = None
    recovery_planned_count: int | None = None
    resource_conflict_count: int | None = None
    active_resource_lock_count: int | None = None
    max_actions: int | None = None
    max_iterations: int | None = None
    max_execution_duration_ms: int | None = None
    max_model_total_tokens: int | None = None
    max_model_estimated_cost_micros: int | None = None


class EvaluationScenarioKind(StrEnum):
    SCENARIO = "scenario"
    REGRESSION = "regression"
    POLICY = "policy"
    RECOVERY = "recovery"
    CROSS_DOMAIN = "cross_domain"
    MULTI_AGENT = "multi_agent"


@dataclass(frozen=True, slots=True)
class EvaluationScenario:
    name: str
    goal: Goal
    task: Task
    expectations: ScenarioExpectations = field(default_factory=ScenarioExpectations)
    kind: EvaluationScenarioKind = EvaluationScenarioKind.SCENARIO
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_non_empty_name("evaluation scenario name", self.name)
        _validate_tags("evaluation scenario tags", self.tags)


@dataclass(frozen=True, slots=True)
class EvaluationScenarioSelector:
    kinds: tuple[EvaluationScenarioKind, ...] | None = None
    tags: tuple[str, ...] = ()
    exclude_tags: tuple[str, ...] = ()

    def matches(self, scenario: EvaluationScenario) -> bool:
        if self.kinds is not None and scenario.kind not in self.kinds:
            return False
        if any(tag not in scenario.tags for tag in self.tags):
            return False
        if any(tag in scenario.tags for tag in self.exclude_tags):
            return False
        return True


@dataclass(frozen=True, slots=True)
class EvaluationSuite:
    name: str
    scenarios: tuple[EvaluationScenario, ...]
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_non_empty_name("evaluation suite name", self.name)
        _validate_tags("evaluation suite tags", self.tags)
        duplicates = _duplicate_values(tuple(scenario.name for scenario in self.scenarios))
        if duplicates:
            raise ValueError("duplicate evaluation scenario names: " + ", ".join(duplicates))

    def select(
        self,
        selector: EvaluationScenarioSelector | None = None,
    ) -> tuple[EvaluationScenario, ...]:
        return select_scenarios(self.scenarios, selector)


@dataclass(frozen=True, slots=True)
class ScenarioCheck:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class ScenarioReport:
    scenario_name: str
    kind: EvaluationScenarioKind
    tags: tuple[str, ...]
    result: ExecutionResult
    session: SessionView
    events: tuple[RuntimeEventView, ...]
    audit_records: tuple[AuditRecordView, ...]
    metrics: RuntimeMetricsView
    checks: tuple[ScenarioCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed_checks(self) -> tuple[ScenarioCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)


@dataclass(frozen=True, slots=True)
class EvaluationSuiteSummary:
    scenario_count: int
    passed_count: int
    failed_count: int
    goal_completed_count: int
    task_completed_count: int
    action_started_count: int
    action_completed_count: int
    tool_failure_count: int
    policy_denial_count: int
    recovery_planned_count: int
    human_intervention_count: int
    resource_lock_acquired_count: int = 0
    resource_lock_released_count: int = 0
    resource_conflict_count: int = 0
    active_resource_lock_count: int = 0
    execution_duration_ms: int = 0
    model_call_count: int = 0
    model_total_token_count: int = 0
    model_estimated_cost_micros: int = 0

    @property
    def pass_rate(self) -> float:
        return _rate(self.passed_count, self.scenario_count)

    @property
    def goal_completion_rate(self) -> float:
        return _rate(self.goal_completed_count, self.scenario_count)

    @property
    def task_success_rate(self) -> float:
        return _rate(self.task_completed_count, self.scenario_count)

    @property
    def action_success_rate(self) -> float:
        successful_actions = self.action_completed_count - self.tool_failure_count
        return _rate(successful_actions, self.action_completed_count)

    @property
    def tool_failure_rate(self) -> float:
        return _rate(self.tool_failure_count, self.action_completed_count)

    @property
    def policy_denial_rate(self) -> float:
        return _rate(self.policy_denial_count, self.scenario_count)

    @property
    def recovery_rate(self) -> float:
        return _rate(self.recovery_planned_count, self.scenario_count)

    @property
    def average_recoveries_per_scenario(self) -> float:
        return _rate(self.recovery_planned_count, self.scenario_count)

    @property
    def human_intervention_rate(self) -> float:
        return _rate(self.human_intervention_count, self.scenario_count)

    @property
    def resource_conflict_rate(self) -> float:
        return _rate(self.resource_conflict_count, self.scenario_count)

    @property
    def average_active_resource_locks_per_scenario(self) -> float:
        return _rate(self.active_resource_lock_count, self.scenario_count)

    @property
    def average_actions_per_scenario(self) -> float:
        return _rate(self.action_started_count, self.scenario_count)

    @property
    def average_execution_duration_ms_per_scenario(self) -> float:
        return _rate(self.execution_duration_ms, self.scenario_count)

    @property
    def average_model_calls_per_scenario(self) -> float:
        return _rate(self.model_call_count, self.scenario_count)

    @property
    def average_model_tokens_per_scenario(self) -> float:
        return _rate(self.model_total_token_count, self.scenario_count)


@dataclass(frozen=True, slots=True)
class EvaluationSuiteReport:
    reports: tuple[ScenarioReport, ...]
    suite_name: str = "evaluation suite"

    @property
    def summary(self) -> EvaluationSuiteSummary:
        return summarize_suite(self.reports)

    @property
    def passed(self) -> bool:
        return all(report.passed for report in self.reports)

    @property
    def failed_reports(self) -> tuple[ScenarioReport, ...]:
        return tuple(report for report in self.reports if not report.passed)

    @property
    def scenario_names(self) -> tuple[str, ...]:
        return tuple(report.scenario_name for report in self.reports)


@dataclass(frozen=True, slots=True)
class EvaluationQualityGate:
    min_pass_rate: float = 1.0
    min_goal_completion_rate: float | None = None
    min_task_success_rate: float | None = None
    min_action_success_rate: float | None = None
    max_tool_failure_rate: float | None = None
    max_policy_denial_rate: float | None = None
    max_average_recoveries_per_scenario: float | None = None
    max_human_intervention_rate: float | None = None
    max_resource_conflict_rate: float | None = None
    max_average_active_resource_locks_per_scenario: float | None = None
    max_average_actions_per_scenario: float | None = None
    max_average_execution_duration_ms_per_scenario: float | None = None
    max_average_model_calls_per_scenario: float | None = None
    max_average_model_tokens_per_scenario: float | None = None
    max_total_model_estimated_cost_micros: int | None = None

    def __post_init__(self) -> None:
        _validate_rate("min_pass_rate", self.min_pass_rate)
        _validate_optional_rate("min_goal_completion_rate", self.min_goal_completion_rate)
        _validate_optional_rate("min_task_success_rate", self.min_task_success_rate)
        _validate_optional_rate("min_action_success_rate", self.min_action_success_rate)
        _validate_optional_rate("max_tool_failure_rate", self.max_tool_failure_rate)
        _validate_optional_rate("max_policy_denial_rate", self.max_policy_denial_rate)
        _validate_optional_non_negative(
            "max_average_recoveries_per_scenario",
            self.max_average_recoveries_per_scenario,
        )
        _validate_optional_rate("max_human_intervention_rate", self.max_human_intervention_rate)
        _validate_optional_rate("max_resource_conflict_rate", self.max_resource_conflict_rate)
        _validate_optional_non_negative(
            "max_average_active_resource_locks_per_scenario",
            self.max_average_active_resource_locks_per_scenario,
        )
        _validate_optional_non_negative(
            "max_average_actions_per_scenario",
            self.max_average_actions_per_scenario,
        )
        _validate_optional_non_negative(
            "max_average_execution_duration_ms_per_scenario",
            self.max_average_execution_duration_ms_per_scenario,
        )
        _validate_optional_non_negative(
            "max_average_model_calls_per_scenario",
            self.max_average_model_calls_per_scenario,
        )
        _validate_optional_non_negative(
            "max_average_model_tokens_per_scenario",
            self.max_average_model_tokens_per_scenario,
        )
        _validate_optional_non_negative(
            "max_total_model_estimated_cost_micros",
            self.max_total_model_estimated_cost_micros,
        )


@dataclass(frozen=True, slots=True)
class EvaluationGateCheck:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class EvaluationGateReport:
    suite_name: str
    summary: EvaluationSuiteSummary
    checks: tuple[EvaluationGateCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed_checks(self) -> tuple[EvaluationGateCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)


class EvaluationHarness:
    """Run behavior scenarios through the application-facing runtime surface.

    The harness does not inspect Kernel internals. It treats RuntimeService-like
    objects as the seam, then verifies observable session, event, metrics and
    audit projections.
    """

    def __init__(self, runtime: EvaluationRuntime) -> None:
        self._runtime = runtime

    async def run(self, scenario: EvaluationScenario) -> ScenarioReport:
        run = await self._runtime.run_goal(scenario.goal, scenario.task)
        session = run.session
        events = await self._runtime.list_events(run.result.session_id)
        audit_records = await self._runtime.audit_records(run.result.session_id)
        summary = await self._session_summary(run.result.session_id)
        metrics = build_runtime_metrics((summary,), events)
        return ScenarioReport(
            scenario.name,
            scenario.kind,
            scenario.tags,
            run.result,
            session,
            events,
            audit_records,
            metrics,
            _evaluate_expectations(
                scenario.expectations,
                run.result,
                session,
                events,
                audit_records,
                metrics,
            ),
        )

    async def run_many(
        self,
        scenarios: tuple[EvaluationScenario, ...],
        *,
        suite_name: str = "evaluation suite",
    ) -> EvaluationSuiteReport:
        if not scenarios:
            raise ValueError("evaluation run requires at least one scenario")
        reports: list[ScenarioReport] = []
        for scenario in scenarios:
            reports.append(await self.run(scenario))
        return EvaluationSuiteReport(tuple(reports), suite_name)

    async def run_suite(
        self,
        suite: EvaluationSuite,
        *,
        selector: EvaluationScenarioSelector | None = None,
    ) -> EvaluationSuiteReport:
        return await self.run_many(suite.select(selector), suite_name=suite.name)

    async def _session_summary(self, session_id: SessionId) -> SessionSummaryView:
        for summary in await self._runtime.list_sessions():
            if summary.session_id == session_id:
                return summary
        raise RuntimeError(f"scenario session was not listed: {session_id}")


def summarize_suite(reports: tuple[ScenarioReport, ...]) -> EvaluationSuiteSummary:
    return EvaluationSuiteSummary(
        scenario_count=len(reports),
        passed_count=sum(1 for report in reports if report.passed),
        failed_count=sum(1 for report in reports if not report.passed),
        goal_completed_count=sum(1 for report in reports if _evaluation_flag(report, "goal")),
        task_completed_count=sum(1 for report in reports if _evaluation_flag(report, "task")),
        action_started_count=sum(report.metrics.action_started_count for report in reports),
        action_completed_count=sum(report.metrics.action_completed_count for report in reports),
        tool_failure_count=sum(report.metrics.tool_failure_count for report in reports),
        policy_denial_count=sum(report.metrics.policy_denial_count for report in reports),
        recovery_planned_count=sum(report.metrics.recovery_planned_count for report in reports),
        human_intervention_count=sum(report.metrics.human_intervention_count for report in reports),
        resource_lock_acquired_count=sum(
            report.metrics.resource_lock_acquired_count for report in reports
        ),
        resource_lock_released_count=sum(
            report.metrics.resource_lock_released_count for report in reports
        ),
        resource_conflict_count=sum(report.metrics.resource_conflict_count for report in reports),
        active_resource_lock_count=sum(
            report.metrics.active_resource_lock_count for report in reports
        ),
        execution_duration_ms=sum(_scenario_duration_ms(report) for report in reports),
        model_call_count=sum(report.metrics.model_call_count for report in reports),
        model_total_token_count=sum(report.metrics.model_total_token_count for report in reports),
        model_estimated_cost_micros=sum(
            report.metrics.model_estimated_cost_micros for report in reports
        ),
    )


def select_scenarios(
    scenarios: tuple[EvaluationScenario, ...],
    selector: EvaluationScenarioSelector | None = None,
) -> tuple[EvaluationScenario, ...]:
    if selector is None:
        return scenarios
    return tuple(scenario for scenario in scenarios if selector.matches(scenario))


def evaluate_quality_gate(
    report: EvaluationSuiteReport,
    gate: EvaluationQualityGate | None = None,
) -> EvaluationGateReport:
    active_gate = EvaluationQualityGate() if gate is None else gate
    summary = report.summary
    checks: list[EvaluationGateCheck] = [
        _gate_minimum("pass_rate", summary.pass_rate, active_gate.min_pass_rate)
    ]
    if active_gate.min_goal_completion_rate is not None:
        checks.append(
            _gate_minimum(
                "goal_completion_rate",
                summary.goal_completion_rate,
                active_gate.min_goal_completion_rate,
            )
        )
    if active_gate.min_task_success_rate is not None:
        checks.append(
            _gate_minimum(
                "task_success_rate",
                summary.task_success_rate,
                active_gate.min_task_success_rate,
            )
        )
    if active_gate.min_action_success_rate is not None:
        checks.append(
            _gate_minimum(
                "action_success_rate",
                summary.action_success_rate,
                active_gate.min_action_success_rate,
            )
        )
    if active_gate.max_tool_failure_rate is not None:
        checks.append(
            _gate_maximum(
                "tool_failure_rate",
                summary.tool_failure_rate,
                active_gate.max_tool_failure_rate,
            )
        )
    if active_gate.max_policy_denial_rate is not None:
        checks.append(
            _gate_maximum(
                "policy_denial_rate",
                summary.policy_denial_rate,
                active_gate.max_policy_denial_rate,
            )
        )
    if active_gate.max_average_recoveries_per_scenario is not None:
        checks.append(
            _gate_maximum(
                "average_recoveries_per_scenario",
                summary.average_recoveries_per_scenario,
                active_gate.max_average_recoveries_per_scenario,
            )
        )
    if active_gate.max_human_intervention_rate is not None:
        checks.append(
            _gate_maximum(
                "human_intervention_rate",
                summary.human_intervention_rate,
                active_gate.max_human_intervention_rate,
            )
        )
    if active_gate.max_resource_conflict_rate is not None:
        checks.append(
            _gate_maximum(
                "resource_conflict_rate",
                summary.resource_conflict_rate,
                active_gate.max_resource_conflict_rate,
            )
        )
    if active_gate.max_average_active_resource_locks_per_scenario is not None:
        checks.append(
            _gate_maximum(
                "average_active_resource_locks_per_scenario",
                summary.average_active_resource_locks_per_scenario,
                active_gate.max_average_active_resource_locks_per_scenario,
            )
        )
    if active_gate.max_average_actions_per_scenario is not None:
        checks.append(
            _gate_maximum(
                "average_actions_per_scenario",
                summary.average_actions_per_scenario,
                active_gate.max_average_actions_per_scenario,
            )
        )
    if active_gate.max_average_execution_duration_ms_per_scenario is not None:
        checks.append(
            _gate_maximum(
                "average_execution_duration_ms_per_scenario",
                summary.average_execution_duration_ms_per_scenario,
                active_gate.max_average_execution_duration_ms_per_scenario,
            )
        )
    if active_gate.max_average_model_calls_per_scenario is not None:
        checks.append(
            _gate_maximum(
                "average_model_calls_per_scenario",
                summary.average_model_calls_per_scenario,
                active_gate.max_average_model_calls_per_scenario,
            )
        )
    if active_gate.max_average_model_tokens_per_scenario is not None:
        checks.append(
            _gate_maximum(
                "average_model_tokens_per_scenario",
                summary.average_model_tokens_per_scenario,
                active_gate.max_average_model_tokens_per_scenario,
            )
        )
    if active_gate.max_total_model_estimated_cost_micros is not None:
        checks.append(
            _gate_maximum(
                "total_model_estimated_cost_micros",
                float(summary.model_estimated_cost_micros),
                float(active_gate.max_total_model_estimated_cost_micros),
            )
        )
    return EvaluationGateReport(report.suite_name, summary, tuple(checks))


def _evaluate_expectations(
    expectations: ScenarioExpectations,
    result: ExecutionResult,
    session: SessionView,
    events: tuple[RuntimeEventView, ...],
    audit_records: tuple[AuditRecordView, ...],
    metrics: RuntimeMetricsView,
) -> tuple[ScenarioCheck, ...]:
    event_types = tuple(event.type for event in events)
    evidence_claims = _evidence_claims(events)
    action_capabilities = _action_capabilities(events)
    audit_capabilities = tuple(record.capability for record in audit_records)
    checks = [
        _check(
            "status",
            result.status is expectations.expected_status,
            f"status={result.status.value}",
            f"expected {expectations.expected_status.value}, got {result.status.value}",
        )
    ]

    if expectations.expected_error_code is not None:
        checks.append(
            _check(
                "error_code",
                result.error_code is expectations.expected_error_code,
                f"error_code={expectations.expected_error_code.value}",
                "expected "
                f"{expectations.expected_error_code.value}, got "
                f"{None if result.error_code is None else result.error_code.value}",
            )
        )

    missing_events = _missing(expectations.required_events, event_types)
    checks.append(
        _check(
            "required_events",
            not missing_events,
            "all required events were observed",
            "missing events: " + ", ".join(missing_events),
        )
    )

    forbidden_events = _present(expectations.forbidden_events, event_types)
    checks.append(
        _check(
            "forbidden_events",
            not forbidden_events,
            "no forbidden events were observed",
            "forbidden events observed: " + ", ".join(forbidden_events),
        )
    )

    missing_evidence = _missing(expectations.required_evidence_claims, evidence_claims)
    checks.append(
        _check(
            "required_evidence_claims",
            not missing_evidence,
            "all required evidence claims were recorded",
            "missing evidence claims: " + ", ".join(missing_evidence),
        )
    )

    forbidden_evidence = _present(expectations.forbidden_evidence_claims, evidence_claims)
    checks.append(
        _check(
            "forbidden_evidence_claims",
            not forbidden_evidence,
            "no forbidden evidence claims were recorded",
            "forbidden evidence claims recorded: " + ", ".join(forbidden_evidence),
        )
    )

    missing_capabilities = _missing(expectations.required_capabilities, action_capabilities)
    checks.append(
        _check(
            "required_capabilities",
            not missing_capabilities,
            "all required capabilities executed",
            "missing capabilities: " + ", ".join(missing_capabilities),
        )
    )

    if expectations.allowed_capabilities is not None:
        unexpected = tuple(
            capability
            for capability in action_capabilities
            if capability not in expectations.allowed_capabilities
        )
        checks.append(
            _check(
                "allowed_capabilities",
                not unexpected,
                "only allowed capabilities executed",
                "unexpected capabilities: " + ", ".join(unexpected),
            )
        )

    missing_audit = _missing(expectations.required_audit_capabilities, audit_capabilities)
    checks.append(
        _check(
            "required_audit_capabilities",
            not missing_audit,
            "all required side-effecting actions were audited",
            "missing audit capabilities: " + ", ".join(missing_audit),
        )
    )

    criteria_mismatches = tuple(
        key
        for key, expected in expectations.expected_criteria.items()
        if session.satisfied_criteria.get(key) != expected
    )
    checks.append(
        _check(
            "expected_criteria",
            not criteria_mismatches,
            "all expected criteria matched",
            "criteria mismatches: " + ", ".join(criteria_mismatches),
        )
    )

    if expectations.policy_denial_count is not None:
        checks.append(
            _check(
                "policy_denial_count",
                metrics.policy_denial_count == expectations.policy_denial_count,
                f"policy_denial_count={metrics.policy_denial_count}",
                f"expected {expectations.policy_denial_count}, got {metrics.policy_denial_count}",
            )
        )

    if expectations.recovery_planned_count is not None:
        checks.append(
            _check(
                "recovery_planned_count",
                metrics.recovery_planned_count == expectations.recovery_planned_count,
                f"recovery_planned_count={metrics.recovery_planned_count}",
                "expected "
                f"{expectations.recovery_planned_count}, got {metrics.recovery_planned_count}",
            )
        )

    if expectations.resource_conflict_count is not None:
        checks.append(
            _check(
                "resource_conflict_count",
                metrics.resource_conflict_count == expectations.resource_conflict_count,
                f"resource_conflict_count={metrics.resource_conflict_count}",
                "expected "
                f"{expectations.resource_conflict_count}, got {metrics.resource_conflict_count}",
            )
        )

    if expectations.active_resource_lock_count is not None:
        checks.append(
            _check(
                "active_resource_lock_count",
                metrics.active_resource_lock_count == expectations.active_resource_lock_count,
                f"active_resource_lock_count={metrics.active_resource_lock_count}",
                "expected "
                f"{expectations.active_resource_lock_count}, got "
                f"{metrics.active_resource_lock_count}",
            )
        )

    if expectations.max_actions is not None:
        checks.append(
            _check(
                "max_actions",
                metrics.action_started_count <= expectations.max_actions,
                f"actions={metrics.action_started_count}",
                f"expected <= {expectations.max_actions}, got {metrics.action_started_count}",
            )
        )

    if expectations.max_iterations is not None:
        checks.append(
            _check(
                "max_iterations",
                result.iterations <= expectations.max_iterations,
                f"iterations={result.iterations}",
                f"expected <= {expectations.max_iterations}, got {result.iterations}",
            )
        )

    if expectations.max_execution_duration_ms is not None:
        duration_ms = _duration_ms(events)
        checks.append(
            _check(
                "max_execution_duration_ms",
                duration_ms <= expectations.max_execution_duration_ms,
                f"execution_duration_ms={duration_ms}",
                f"expected <= {expectations.max_execution_duration_ms}, got {duration_ms}",
            )
        )

    if expectations.max_model_total_tokens is not None:
        checks.append(
            _check(
                "max_model_total_tokens",
                metrics.model_total_token_count <= expectations.max_model_total_tokens,
                f"model_total_tokens={metrics.model_total_token_count}",
                "expected <= "
                f"{expectations.max_model_total_tokens}, got "
                f"{metrics.model_total_token_count}",
            )
        )

    if expectations.max_model_estimated_cost_micros is not None:
        checks.append(
            _check(
                "max_model_estimated_cost_micros",
                metrics.model_estimated_cost_micros <= expectations.max_model_estimated_cost_micros,
                f"model_estimated_cost_micros={metrics.model_estimated_cost_micros}",
                "expected <= "
                f"{expectations.max_model_estimated_cost_micros}, got "
                f"{metrics.model_estimated_cost_micros}",
            )
        )

    return tuple(checks)


def _evidence_claims(events: tuple[RuntimeEventView, ...]) -> tuple[str, ...]:
    claims: list[str] = []
    for event in events:
        if event.type != "EvidenceRecorded":
            continue
        claim = event.data.get("claim")
        if isinstance(claim, str):
            claims.append(claim)
    return tuple(claims)


def _action_capabilities(events: tuple[RuntimeEventView, ...]) -> tuple[str, ...]:
    capabilities: list[str] = []
    for event in events:
        if event.type != "ActionStarted":
            continue
        capability = event.data.get("capability")
        if isinstance(capability, str):
            capabilities.append(capability)
    return tuple(capabilities)


def _missing(required: tuple[str, ...], actual: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(item for item in required if item not in actual)


def _present(forbidden: tuple[str, ...], actual: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(item for item in forbidden if item in actual)


def _check(
    name: str,
    passed: bool,
    success_message: str,
    failure_message: str,
) -> ScenarioCheck:
    return ScenarioCheck(name, passed, success_message if passed else failure_message)


def _gate_minimum(name: str, actual: float, minimum: float) -> EvaluationGateCheck:
    passed = actual >= minimum
    return EvaluationGateCheck(
        name,
        passed,
        f"{name}={actual:.3f} >= {minimum:.3f}"
        if passed
        else f"expected {name} >= {minimum:.3f}, got {actual:.3f}",
    )


def _gate_maximum(name: str, actual: float, maximum: float) -> EvaluationGateCheck:
    passed = actual <= maximum
    return EvaluationGateCheck(
        name,
        passed,
        f"{name}={actual:.3f} <= {maximum:.3f}"
        if passed
        else f"expected {name} <= {maximum:.3f}, got {actual:.3f}",
    )


def _validate_optional_rate(name: str, value: float | None) -> None:
    if value is not None:
        _validate_rate(name, value)


def _validate_rate(name: str, value: float) -> None:
    if value < 0.0 or value > 1.0:
        raise ValueError(f"{name} must be between 0.0 and 1.0")


def _validate_optional_non_negative(name: str, value: float | int | None) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{name} must be non-negative")


def _validate_non_empty_name(field: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{field} must not be empty")


def _validate_tags(field: str, tags: tuple[str, ...]) -> None:
    empty = tuple(tag for tag in tags if not tag.strip())
    if empty:
        raise ValueError(f"{field} must not contain empty values")
    duplicates = _duplicate_values(tags)
    if duplicates:
        raise ValueError(f"duplicate {field}: " + ", ".join(duplicates))


def _duplicate_values(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return tuple(sorted(duplicates))


def _evaluation_flag(report: ScenarioReport, flag: str) -> bool:
    evaluation = report.session.latest_evaluation
    if evaluation is None:
        return False
    if flag == "goal":
        return evaluation.goal_completed
    if flag == "task":
        return evaluation.task_completed
    raise ValueError(f"unknown evaluation flag: {flag}")


def _scenario_duration_ms(report: ScenarioReport) -> int:
    return _duration_ms(report.events)


def _duration_ms(events: tuple[RuntimeEventView, ...]) -> int:
    if not events:
        return 0
    start = min(event.occurred_at for event in events)
    end = max(event.occurred_at for event in events)
    return round((end - start).total_seconds() * 1000)


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator
