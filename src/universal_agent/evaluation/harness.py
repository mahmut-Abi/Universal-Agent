from __future__ import annotations

from dataclasses import dataclass, field
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
    required_capabilities: tuple[str, ...] = ()
    allowed_capabilities: tuple[str, ...] | None = None
    required_audit_capabilities: tuple[str, ...] = ()
    policy_denial_count: int | None = None
    recovery_planned_count: int | None = None
    max_actions: int | None = None
    max_iterations: int | None = None


@dataclass(frozen=True, slots=True)
class EvaluationScenario:
    name: str
    goal: Goal
    task: Task
    expectations: ScenarioExpectations = field(default_factory=ScenarioExpectations)


@dataclass(frozen=True, slots=True)
class ScenarioCheck:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class ScenarioReport:
    scenario_name: str
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
class EvaluationSuiteReport:
    reports: tuple[ScenarioReport, ...]

    @property
    def passed(self) -> bool:
        return all(report.passed for report in self.reports)

    @property
    def failed_reports(self) -> tuple[ScenarioReport, ...]:
        return tuple(report for report in self.reports if not report.passed)


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

    async def run_many(self, scenarios: tuple[EvaluationScenario, ...]) -> EvaluationSuiteReport:
        reports: list[ScenarioReport] = []
        for scenario in scenarios:
            reports.append(await self.run(scenario))
        return EvaluationSuiteReport(tuple(reports))

    async def _session_summary(self, session_id: SessionId) -> SessionSummaryView:
        for summary in await self._runtime.list_sessions():
            if summary.session_id == session_id:
                return summary
        raise RuntimeError(f"scenario session was not listed: {session_id}")


def _evaluate_expectations(
    expectations: ScenarioExpectations,
    result: ExecutionResult,
    session: SessionView,
    events: tuple[RuntimeEventView, ...],
    audit_records: tuple[AuditRecordView, ...],
    metrics: RuntimeMetricsView,
) -> tuple[ScenarioCheck, ...]:
    event_types = tuple(event.type for event in events)
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
                "expected "
                f"{expectations.policy_denial_count}, got {metrics.policy_denial_count}",
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

    return tuple(checks)


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
