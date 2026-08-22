from __future__ import annotations

from dataclasses import dataclass, field

from universal_agent.core import (
    ErrorCode,
    ExecutionStatus,
    JsonMapping,
    JsonValue,
    immutable_json,
)
from universal_agent.evaluation.harness import (
    EvaluationHarness,
    EvaluationRuntime,
    EvaluationScenario,
    ScenarioReport,
)


@dataclass(frozen=True, slots=True)
class ReplayMetrics:
    event_count: int
    action_started_count: int
    action_completed_count: int
    tool_failure_count: int
    policy_denial_count: int
    confirmation_required_count: int
    recovery_planned_count: int
    recovery_exhausted_count: int
    human_intervention_count: int
    resource_lock_acquired_count: int = 0
    resource_lock_released_count: int = 0
    resource_conflict_count: int = 0
    active_resource_lock_count: int = 0
    model_call_count: int = 0
    model_total_token_count: int = 0
    model_estimated_cost_micros: int = 0


@dataclass(frozen=True, slots=True)
class ReplayAuditEntry:
    capability: str
    tool_name: str
    policy_effect: str
    status: str
    error_code: ErrorCode | None


@dataclass(frozen=True, slots=True)
class ReplayRecording:
    """Stable behavior trace for deterministic scenario replay.

    Dynamic identifiers and timestamps are intentionally excluded. The
    recording captures externally meaningful behavior: terminal status,
    criteria, event shape, action choices, policy outcomes, audit results and
    metrics.
    """

    scenario_name: str
    result_status: ExecutionStatus
    error_code: ErrorCode | None
    satisfied_criteria: JsonMapping = field(default_factory=immutable_json)
    event_types: tuple[str, ...] = ()
    action_capabilities: tuple[str, ...] = ()
    action_statuses: tuple[str, ...] = ()
    policy_effects: tuple[str, ...] = ()
    audit_entries: tuple[ReplayAuditEntry, ...] = ()
    metrics: ReplayMetrics = field(default_factory=lambda: ReplayMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0))


@dataclass(frozen=True, slots=True)
class ReplayCheck:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class ReplayReport:
    expected: ReplayRecording
    actual: ReplayRecording
    checks: tuple[ReplayCheck, ...]
    scenario_report: ScenarioReport

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed_checks(self) -> tuple[ReplayCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)


class DeterministicReplayHarness:
    """Record and replay scenarios through the same harness seam."""

    def __init__(self, runtime: EvaluationRuntime) -> None:
        self._harness = EvaluationHarness(runtime)

    async def record(self, scenario: EvaluationScenario) -> ReplayRecording:
        return record_report(await self._harness.run(scenario))

    async def replay(
        self,
        scenario: EvaluationScenario,
        expected: ReplayRecording,
    ) -> ReplayReport:
        report = await self._harness.run(scenario)
        return compare_recording(expected, report)


def record_report(report: ScenarioReport) -> ReplayRecording:
    return ReplayRecording(
        scenario_name=report.scenario_name,
        result_status=report.result.status,
        error_code=report.result.error_code,
        satisfied_criteria=immutable_json(report.session.satisfied_criteria),
        event_types=tuple(event.type for event in report.events),
        action_capabilities=_event_values(report, "ActionStarted", "capability"),
        action_statuses=_event_values(report, "ActionCompleted", "status"),
        policy_effects=_event_values(report, "PolicyChecked", "effect"),
        audit_entries=tuple(
            ReplayAuditEntry(
                record.capability,
                record.tool_name,
                record.policy_effect,
                record.status,
                record.error_code,
            )
            for record in report.audit_records
        ),
        metrics=ReplayMetrics(
            event_count=report.metrics.event_count,
            action_started_count=report.metrics.action_started_count,
            action_completed_count=report.metrics.action_completed_count,
            tool_failure_count=report.metrics.tool_failure_count,
            policy_denial_count=report.metrics.policy_denial_count,
            confirmation_required_count=report.metrics.confirmation_required_count,
            recovery_planned_count=report.metrics.recovery_planned_count,
            recovery_exhausted_count=report.metrics.recovery_exhausted_count,
            human_intervention_count=report.metrics.human_intervention_count,
            resource_lock_acquired_count=report.metrics.resource_lock_acquired_count,
            resource_lock_released_count=report.metrics.resource_lock_released_count,
            resource_conflict_count=report.metrics.resource_conflict_count,
            active_resource_lock_count=report.metrics.active_resource_lock_count,
            model_call_count=report.metrics.model_call_count,
            model_total_token_count=report.metrics.model_total_token_count,
            model_estimated_cost_micros=report.metrics.model_estimated_cost_micros,
        ),
    )


def compare_recording(expected: ReplayRecording, actual_report: ScenarioReport) -> ReplayReport:
    actual = record_report(actual_report)
    checks = (
        _same("scenario_name", expected.scenario_name, actual.scenario_name),
        _same("result_status", expected.result_status, actual.result_status),
        _same("error_code", expected.error_code, actual.error_code),
        _same_mapping(
            "satisfied_criteria",
            expected.satisfied_criteria,
            actual.satisfied_criteria,
        ),
        _same("event_types", expected.event_types, actual.event_types),
        _same("action_capabilities", expected.action_capabilities, actual.action_capabilities),
        _same("action_statuses", expected.action_statuses, actual.action_statuses),
        _same("policy_effects", expected.policy_effects, actual.policy_effects),
        _same("audit_entries", expected.audit_entries, actual.audit_entries),
        _same("metrics", expected.metrics, actual.metrics),
    )
    return ReplayReport(expected, actual, checks, actual_report)


def _event_values(
    report: ScenarioReport,
    event_type: str,
    data_key: str,
) -> tuple[str, ...]:
    values: list[str] = []
    for event in report.events:
        if event.type != event_type:
            continue
        value = event.data.get(data_key)
        if isinstance(value, str):
            values.append(value)
    return tuple(values)


def _same(name: str, expected: object, actual: object) -> ReplayCheck:
    return ReplayCheck(
        name,
        expected == actual,
        "matched" if expected == actual else f"expected {expected!r}, got {actual!r}",
    )


def _same_mapping(
    name: str,
    expected: JsonMapping,
    actual: JsonMapping,
) -> ReplayCheck:
    expected_dict: dict[str, JsonValue] = dict(expected)
    actual_dict: dict[str, JsonValue] = dict(actual)
    passed = expected_dict == actual_dict
    message = "matched" if passed else f"expected {expected_dict!r}, got {actual_dict!r}"
    return ReplayCheck(
        name,
        passed,
        message,
    )
