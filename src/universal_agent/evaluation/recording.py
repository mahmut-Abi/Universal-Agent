from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError

from universal_agent.core import ErrorCode, ExecutionStatus, JsonMapping, JsonValue, immutable_json
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
)
from universal_agent.core.config_validation import (
    json_mapping as _json_mapping,
)
from universal_agent.core.config_validation import (
    parse_json_object as _parse_json_object,
)
from universal_agent.evaluation.harness import (
    EvaluationGateReport,
    EvaluationScenarioKind,
    EvaluationSuiteReport,
    ScenarioReport,
)
from universal_agent.evaluation.junit import (
    encode_evaluation_junit_xml as encode_evaluation_junit_xml,
)
from universal_agent.evaluation.replay import ReplayAuditEntry, ReplayMetrics, ReplayRecording

EVALUATION_REPORT_SCHEMA_VERSION = 3
REPLAY_RECORDING_SCHEMA_VERSION = 1
JsonObject = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class EvaluationCheckRecording:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class EvaluationSummaryRecording:
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


@dataclass(frozen=True, slots=True)
class EvaluationScenarioRecording:
    scenario_name: str
    passed: bool
    result_status: ExecutionStatus
    error_code: ErrorCode | None
    kind: EvaluationScenarioKind = EvaluationScenarioKind.SCENARIO
    tags: tuple[str, ...] = ()
    satisfied_criteria: JsonMapping = field(default_factory=immutable_json)
    checks: tuple[EvaluationCheckRecording, ...] = ()
    event_types: tuple[str, ...] = ()
    action_capabilities: tuple[str, ...] = ()
    audit_capabilities: tuple[str, ...] = ()
    evidence_claims: tuple[str, ...] = ()
    metrics: ReplayMetrics = field(default_factory=lambda: ReplayMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0))

    def __post_init__(self) -> None:
        _validate_non_empty_name("evaluation scenario recording name", self.scenario_name)
        _validate_tags("evaluation scenario recording tags", self.tags)


@dataclass(frozen=True, slots=True)
class EvaluationGateRecording:
    passed: bool
    checks: tuple[EvaluationCheckRecording, ...] = ()


@dataclass(frozen=True, slots=True)
class EvaluationReportRecording:
    suite_name: str
    passed: bool
    summary: EvaluationSummaryRecording
    scenarios: tuple[EvaluationScenarioRecording, ...]
    gate: EvaluationGateRecording | None = None

    def __post_init__(self) -> None:
        _validate_non_empty_name("evaluation report suite name", self.suite_name)
        duplicates = _duplicate_values(tuple(scenario.scenario_name for scenario in self.scenarios))
        if duplicates:
            raise ValueError(
                "duplicate evaluation scenario recording names: " + ", ".join(duplicates)
            )


class _EvaluationCheckPayload(ConfigPayload):
    name: str
    passed: bool
    message: str


class _EvaluationSummaryPayload(ConfigPayload):
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


class _ReplayMetricsPayload(ConfigPayload):
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
    decision_generated_count: int = 0
    decision_validated_count: int = 0
    decision_rejected_count: int = 0
    model_call_count: int = 0
    model_total_token_count: int = 0
    model_estimated_cost_micros: int = 0


class _EvaluationScenarioPayload(ConfigPayload):
    scenario_name: str
    kind: str = EvaluationScenarioKind.SCENARIO.value
    tags: list[str] = Field(default_factory=list)
    passed: bool
    result_status: str
    error_code: str | None
    satisfied_criteria: dict[str, PydanticJsonValue]
    checks: list[_EvaluationCheckPayload]
    event_types: list[str]
    action_capabilities: list[str]
    audit_capabilities: list[str]
    evidence_claims: list[str] = Field(default_factory=list)
    metrics: _ReplayMetricsPayload


class _EvaluationGatePayload(ConfigPayload):
    passed: bool
    checks: list[_EvaluationCheckPayload]


class _EvaluationReportPayload(ConfigPayload):
    schema_version: int
    suite_name: str
    passed: bool
    summary: _EvaluationSummaryPayload
    scenarios: list[_EvaluationScenarioPayload]
    gate: _EvaluationGatePayload | None = None


class _ReplayAuditEntryPayload(ConfigPayload):
    capability: str
    tool_name: str
    policy_effect: str
    status: str
    error_code: str | None


class _ReplayRecordingPayload(ConfigPayload):
    schema_version: int
    scenario_name: str
    result_status: str
    error_code: str | None
    satisfied_criteria: dict[str, PydanticJsonValue]
    event_types: list[str]
    action_capabilities: list[str]
    action_statuses: list[str]
    policy_effects: list[str]
    audit_entries: list[_ReplayAuditEntryPayload]
    metrics: _ReplayMetricsPayload


@dataclass(frozen=True, slots=True)
class EvaluationReportComparisonCheck:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class EvaluationReportComparison:
    expected: EvaluationReportRecording
    actual: EvaluationReportRecording
    checks: tuple[EvaluationReportComparisonCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed_checks(self) -> tuple[EvaluationReportComparisonCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)


class EvaluationReportNotFoundError(LookupError):
    pass


class ReplayRecordingNotFoundError(LookupError):
    pass


class EvaluationReportStore(Protocol):
    def save(self, recording: EvaluationReportRecording) -> None: ...

    def load(self, suite_name: str) -> EvaluationReportRecording: ...

    def list_reports(self) -> tuple[EvaluationReportRecording, ...]: ...


class ReplayRecordingStore(Protocol):
    def save(self, recording: ReplayRecording) -> None: ...

    def load(self, scenario_name: str) -> ReplayRecording: ...

    def list_recordings(self) -> tuple[ReplayRecording, ...]: ...


class FileEvaluationReportStore:
    """File-backed evaluation report store for local CI and regression reports."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def save(self, recording: EvaluationReportRecording) -> None:
        path = self._path(recording.suite_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(encode_evaluation_report(recording), handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(path)

    def load(self, suite_name: str) -> EvaluationReportRecording:
        path = self._path(suite_name)
        if not path.exists():
            raise EvaluationReportNotFoundError(f"evaluation report not found: {suite_name}")
        with path.open("r", encoding="utf-8") as handle:
            return decode_evaluation_report(json_mapping(json.load(handle)))

    def list_reports(self) -> tuple[EvaluationReportRecording, ...]:
        if not self._root.exists():
            return ()
        reports = tuple(
            decode_evaluation_report(json_mapping(json.loads(path.read_text(encoding="utf-8"))))
            for path in sorted(self._root.glob("*.json"))
        )
        return tuple(sorted(reports, key=lambda item: item.suite_name))

    def _path(self, suite_name: str) -> Path:
        return self._root / f"{quote(suite_name, safe='')}.json"


class FileReplayRecordingStore:
    """File-backed golden recording store for local regression tests."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def save(self, recording: ReplayRecording) -> None:
        path = self._path(recording.scenario_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(encode_replay_recording(recording), handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(path)

    def load(self, scenario_name: str) -> ReplayRecording:
        path = self._path(scenario_name)
        if not path.exists():
            raise ReplayRecordingNotFoundError(f"replay recording not found: {scenario_name}")
        with path.open("r", encoding="utf-8") as handle:
            return decode_replay_recording(json_mapping(json.load(handle)))

    def list_recordings(self) -> tuple[ReplayRecording, ...]:
        if not self._root.exists():
            return ()
        recordings = tuple(
            decode_replay_recording(json_mapping(json.loads(path.read_text(encoding="utf-8"))))
            for path in sorted(self._root.glob("*.json"))
        )
        return tuple(sorted(recordings, key=lambda item: item.scenario_name))

    def _path(self, scenario_name: str) -> Path:
        return self._root / f"{quote(scenario_name, safe='')}.json"


def record_evaluation_suite(
    report: EvaluationSuiteReport,
    *,
    suite_name: str | None = None,
    gate_report: EvaluationGateReport | None = None,
) -> EvaluationReportRecording:
    summary = report.summary
    return EvaluationReportRecording(
        suite_name=report.suite_name if suite_name is None else suite_name,
        passed=report.passed,
        summary=EvaluationSummaryRecording(
            scenario_count=summary.scenario_count,
            passed_count=summary.passed_count,
            failed_count=summary.failed_count,
            goal_completed_count=summary.goal_completed_count,
            task_completed_count=summary.task_completed_count,
            action_started_count=summary.action_started_count,
            action_completed_count=summary.action_completed_count,
            tool_failure_count=summary.tool_failure_count,
            policy_denial_count=summary.policy_denial_count,
            recovery_planned_count=summary.recovery_planned_count,
            human_intervention_count=summary.human_intervention_count,
            resource_lock_acquired_count=summary.resource_lock_acquired_count,
            resource_lock_released_count=summary.resource_lock_released_count,
            resource_conflict_count=summary.resource_conflict_count,
            active_resource_lock_count=summary.active_resource_lock_count,
            execution_duration_ms=summary.execution_duration_ms,
            model_call_count=summary.model_call_count,
            model_total_token_count=summary.model_total_token_count,
            model_estimated_cost_micros=summary.model_estimated_cost_micros,
        ),
        scenarios=tuple(record_evaluation_scenario(item) for item in report.reports),
        gate=None if gate_report is None else record_evaluation_gate(gate_report),
    )


def record_evaluation_gate(report: EvaluationGateReport) -> EvaluationGateRecording:
    return EvaluationGateRecording(
        passed=report.passed,
        checks=tuple(
            EvaluationCheckRecording(check.name, check.passed, check.message)
            for check in report.checks
        ),
    )


def record_evaluation_scenario(report: ScenarioReport) -> EvaluationScenarioRecording:
    return EvaluationScenarioRecording(
        scenario_name=report.scenario_name,
        kind=report.kind,
        tags=report.tags,
        passed=report.passed,
        result_status=report.result.status,
        error_code=report.result.error_code,
        satisfied_criteria=immutable_json(report.session.satisfied_criteria),
        checks=tuple(
            EvaluationCheckRecording(check.name, check.passed, check.message)
            for check in report.checks
        ),
        event_types=tuple(event.type for event in report.events),
        action_capabilities=_event_values(report, "ActionStarted", "capability"),
        audit_capabilities=tuple(record.capability for record in report.audit_records),
        evidence_claims=_event_values(report, "EvidenceRecorded", "claim"),
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
            decision_generated_count=report.metrics.decision_generated_count,
            decision_validated_count=report.metrics.decision_validated_count,
            decision_rejected_count=report.metrics.decision_rejected_count,
            model_call_count=report.metrics.model_call_count,
            model_total_token_count=report.metrics.model_total_token_count,
            model_estimated_cost_micros=report.metrics.model_estimated_cost_micros,
        ),
    )


def compare_evaluation_reports(
    expected: EvaluationReportRecording,
    actual: EvaluationReportRecording,
) -> EvaluationReportComparison:
    checks: list[EvaluationReportComparisonCheck] = [
        _comparison_check("suite_name", expected.suite_name, actual.suite_name),
        _comparison_check("passed", expected.passed, actual.passed),
        _comparison_check("summary", expected.summary, actual.summary),
        _comparison_check(
            "scenario_names",
            tuple(item.scenario_name for item in expected.scenarios),
            tuple(item.scenario_name for item in actual.scenarios),
        ),
        _comparison_check("gate_presence", expected.gate is not None, actual.gate is not None),
    ]
    expected_scenarios = {scenario.scenario_name: scenario for scenario in expected.scenarios}
    actual_scenarios = {scenario.scenario_name: scenario for scenario in actual.scenarios}
    for scenario_name in expected_scenarios:
        actual_scenario = actual_scenarios.get(scenario_name)
        if actual_scenario is None:
            checks.append(
                EvaluationReportComparisonCheck(
                    f"scenario:{scenario_name}",
                    False,
                    "missing actual scenario",
                )
            )
            continue
        checks.extend(_compare_scenario(expected_scenarios[scenario_name], actual_scenario))
    if expected.gate is not None and actual.gate is not None:
        checks.extend(_compare_gate(expected.gate, actual.gate))
    return EvaluationReportComparison(expected, actual, tuple(checks))


def _compare_scenario(
    expected: EvaluationScenarioRecording,
    actual: EvaluationScenarioRecording,
) -> tuple[EvaluationReportComparisonCheck, ...]:
    prefix = f"scenario:{expected.scenario_name}"
    return (
        _comparison_check(f"{prefix}:passed", expected.passed, actual.passed),
        _comparison_check(f"{prefix}:result_status", expected.result_status, actual.result_status),
        _comparison_check(f"{prefix}:error_code", expected.error_code, actual.error_code),
        _comparison_check(
            f"{prefix}:satisfied_criteria",
            dict(expected.satisfied_criteria),
            dict(actual.satisfied_criteria),
        ),
        _comparison_check(f"{prefix}:kind", expected.kind, actual.kind),
        _comparison_check(f"{prefix}:tags", expected.tags, actual.tags),
        _comparison_check(f"{prefix}:event_types", expected.event_types, actual.event_types),
        _comparison_check(
            f"{prefix}:action_capabilities",
            expected.action_capabilities,
            actual.action_capabilities,
        ),
        _comparison_check(
            f"{prefix}:audit_capabilities",
            expected.audit_capabilities,
            actual.audit_capabilities,
        ),
        _comparison_check(
            f"{prefix}:evidence_claims",
            expected.evidence_claims,
            actual.evidence_claims,
        ),
        _comparison_check(f"{prefix}:metrics", expected.metrics, actual.metrics),
    )


def _compare_gate(
    expected: EvaluationGateRecording,
    actual: EvaluationGateRecording,
) -> tuple[EvaluationReportComparisonCheck, ...]:
    return (
        _comparison_check("gate:passed", expected.passed, actual.passed),
        _comparison_check(
            "gate:check_names",
            tuple(check.name for check in expected.checks),
            tuple(check.name for check in actual.checks),
        ),
        _comparison_check("gate:checks", expected.checks, actual.checks),
    )


def _comparison_check(
    name: str,
    expected: object,
    actual: object,
) -> EvaluationReportComparisonCheck:
    passed = expected == actual
    return EvaluationReportComparisonCheck(
        name,
        passed,
        "matched" if passed else f"expected {expected!r}, got {actual!r}",
    )


def _event_values(report: ScenarioReport, event_type: str, data_key: str) -> tuple[str, ...]:
    values: list[str] = []
    for event in report.events:
        if event.type != event_type:
            continue
        value = event.data.get(data_key)
        if isinstance(value, str):
            values.append(value)
    return tuple(values)


def encode_evaluation_report(recording: EvaluationReportRecording) -> JsonObject:
    return {
        "schema_version": EVALUATION_REPORT_SCHEMA_VERSION,
        "suite_name": recording.suite_name,
        "passed": recording.passed,
        "summary": _encode_evaluation_summary(recording.summary),
        "scenarios": [_encode_evaluation_scenario(scenario) for scenario in recording.scenarios],
        "gate": None if recording.gate is None else _encode_evaluation_gate(recording.gate),
    }


def decode_evaluation_report(payload: Mapping[str, JsonValue]) -> EvaluationReportRecording:
    report = _parse_recording_payload(_EvaluationReportPayload, payload)
    version = report.schema_version
    if version not in (1, 2, EVALUATION_REPORT_SCHEMA_VERSION):
        raise ValueError(f"unsupported evaluation report schema version: {version}")
    return EvaluationReportRecording(
        suite_name=report.suite_name,
        passed=report.passed,
        summary=_decode_evaluation_summary(report.summary),
        scenarios=tuple(_decode_evaluation_scenario(item) for item in report.scenarios),
        gate=None if report.gate is None else _decode_evaluation_gate(report.gate),
    )


def encode_replay_recording(recording: ReplayRecording) -> JsonObject:
    return {
        "schema_version": REPLAY_RECORDING_SCHEMA_VERSION,
        "scenario_name": recording.scenario_name,
        "result_status": recording.result_status.value,
        "error_code": None if recording.error_code is None else recording.error_code.value,
        "satisfied_criteria": _to_json(recording.satisfied_criteria),
        "event_types": list(recording.event_types),
        "action_capabilities": list(recording.action_capabilities),
        "action_statuses": list(recording.action_statuses),
        "policy_effects": list(recording.policy_effects),
        "audit_entries": [
            {
                "capability": item.capability,
                "tool_name": item.tool_name,
                "policy_effect": item.policy_effect,
                "status": item.status,
                "error_code": None if item.error_code is None else item.error_code.value,
            }
            for item in recording.audit_entries
        ],
        "metrics": _encode_metrics(recording.metrics),
    }


def decode_replay_recording(payload: Mapping[str, JsonValue]) -> ReplayRecording:
    recording = _parse_recording_payload(_ReplayRecordingPayload, payload)
    version = recording.schema_version
    if version != REPLAY_RECORDING_SCHEMA_VERSION:
        raise ValueError(f"unsupported replay recording schema version: {version}")
    return ReplayRecording(
        scenario_name=recording.scenario_name,
        result_status=ExecutionStatus(recording.result_status),
        error_code=_optional_error(recording.error_code),
        satisfied_criteria=immutable_json(_json_mapping(recording.satisfied_criteria)),
        event_types=_string_tuple(recording.event_types),
        action_capabilities=_string_tuple(recording.action_capabilities),
        action_statuses=_string_tuple(recording.action_statuses),
        policy_effects=_string_tuple(recording.policy_effects),
        audit_entries=tuple(_decode_audit_entry(item) for item in recording.audit_entries),
        metrics=_decode_metrics(recording.metrics),
    )


def json_mapping(value: object) -> JsonMapping:
    return _parse_json_object(value, "evaluation recording")


def _encode_evaluation_summary(summary: EvaluationSummaryRecording) -> JsonObject:
    return {
        "scenario_count": summary.scenario_count,
        "passed_count": summary.passed_count,
        "failed_count": summary.failed_count,
        "goal_completed_count": summary.goal_completed_count,
        "task_completed_count": summary.task_completed_count,
        "action_started_count": summary.action_started_count,
        "action_completed_count": summary.action_completed_count,
        "tool_failure_count": summary.tool_failure_count,
        "policy_denial_count": summary.policy_denial_count,
        "recovery_planned_count": summary.recovery_planned_count,
        "human_intervention_count": summary.human_intervention_count,
        "resource_lock_acquired_count": summary.resource_lock_acquired_count,
        "resource_lock_released_count": summary.resource_lock_released_count,
        "resource_conflict_count": summary.resource_conflict_count,
        "active_resource_lock_count": summary.active_resource_lock_count,
        "execution_duration_ms": summary.execution_duration_ms,
        "model_call_count": summary.model_call_count,
        "model_total_token_count": summary.model_total_token_count,
        "model_estimated_cost_micros": summary.model_estimated_cost_micros,
    }


def _decode_evaluation_summary(payload: _EvaluationSummaryPayload) -> EvaluationSummaryRecording:
    return EvaluationSummaryRecording(
        scenario_count=payload.scenario_count,
        passed_count=payload.passed_count,
        failed_count=payload.failed_count,
        goal_completed_count=payload.goal_completed_count,
        task_completed_count=payload.task_completed_count,
        action_started_count=payload.action_started_count,
        action_completed_count=payload.action_completed_count,
        tool_failure_count=payload.tool_failure_count,
        policy_denial_count=payload.policy_denial_count,
        recovery_planned_count=payload.recovery_planned_count,
        human_intervention_count=payload.human_intervention_count,
        resource_lock_acquired_count=payload.resource_lock_acquired_count,
        resource_lock_released_count=payload.resource_lock_released_count,
        resource_conflict_count=payload.resource_conflict_count,
        active_resource_lock_count=payload.active_resource_lock_count,
        execution_duration_ms=payload.execution_duration_ms,
        model_call_count=payload.model_call_count,
        model_total_token_count=payload.model_total_token_count,
        model_estimated_cost_micros=payload.model_estimated_cost_micros,
    )


def _encode_evaluation_scenario(scenario: EvaluationScenarioRecording) -> JsonObject:
    return {
        "scenario_name": scenario.scenario_name,
        "kind": scenario.kind.value,
        "tags": list(scenario.tags),
        "passed": scenario.passed,
        "result_status": scenario.result_status.value,
        "error_code": None if scenario.error_code is None else scenario.error_code.value,
        "satisfied_criteria": _to_json(scenario.satisfied_criteria),
        "checks": [_encode_evaluation_check(check) for check in scenario.checks],
        "event_types": list(scenario.event_types),
        "action_capabilities": list(scenario.action_capabilities),
        "audit_capabilities": list(scenario.audit_capabilities),
        "evidence_claims": list(scenario.evidence_claims),
        "metrics": _encode_metrics(scenario.metrics),
    }


def _decode_evaluation_scenario(payload: _EvaluationScenarioPayload) -> EvaluationScenarioRecording:
    return EvaluationScenarioRecording(
        scenario_name=payload.scenario_name,
        kind=EvaluationScenarioKind(payload.kind),
        tags=_string_tuple(payload.tags),
        passed=payload.passed,
        result_status=ExecutionStatus(payload.result_status),
        error_code=_optional_error(payload.error_code),
        satisfied_criteria=immutable_json(_json_mapping(payload.satisfied_criteria)),
        checks=tuple(_decode_evaluation_check(item) for item in payload.checks),
        event_types=_string_tuple(payload.event_types),
        action_capabilities=_string_tuple(payload.action_capabilities),
        audit_capabilities=_string_tuple(payload.audit_capabilities),
        evidence_claims=_string_tuple(payload.evidence_claims),
        metrics=_decode_metrics(payload.metrics),
    )


def _encode_evaluation_gate(gate: EvaluationGateRecording) -> JsonObject:
    return {
        "passed": gate.passed,
        "checks": [_encode_evaluation_check(check) for check in gate.checks],
    }


def _decode_evaluation_gate(payload: _EvaluationGatePayload) -> EvaluationGateRecording:
    return EvaluationGateRecording(
        passed=payload.passed,
        checks=tuple(_decode_evaluation_check(item) for item in payload.checks),
    )


def _encode_evaluation_check(check: EvaluationCheckRecording) -> JsonObject:
    return {"name": check.name, "passed": check.passed, "message": check.message}


def _decode_evaluation_check(payload: _EvaluationCheckPayload) -> EvaluationCheckRecording:
    return EvaluationCheckRecording(
        name=payload.name,
        passed=payload.passed,
        message=payload.message,
    )


def _encode_metrics(metrics: ReplayMetrics) -> JsonObject:
    return {
        "event_count": metrics.event_count,
        "action_started_count": metrics.action_started_count,
        "action_completed_count": metrics.action_completed_count,
        "tool_failure_count": metrics.tool_failure_count,
        "policy_denial_count": metrics.policy_denial_count,
        "confirmation_required_count": metrics.confirmation_required_count,
        "recovery_planned_count": metrics.recovery_planned_count,
        "recovery_exhausted_count": metrics.recovery_exhausted_count,
        "human_intervention_count": metrics.human_intervention_count,
        "resource_lock_acquired_count": metrics.resource_lock_acquired_count,
        "resource_lock_released_count": metrics.resource_lock_released_count,
        "resource_conflict_count": metrics.resource_conflict_count,
        "active_resource_lock_count": metrics.active_resource_lock_count,
        "decision_generated_count": metrics.decision_generated_count,
        "decision_validated_count": metrics.decision_validated_count,
        "decision_rejected_count": metrics.decision_rejected_count,
        "model_call_count": metrics.model_call_count,
        "model_total_token_count": metrics.model_total_token_count,
        "model_estimated_cost_micros": metrics.model_estimated_cost_micros,
    }


def _decode_audit_entry(payload: _ReplayAuditEntryPayload) -> ReplayAuditEntry:
    return ReplayAuditEntry(
        capability=payload.capability,
        tool_name=payload.tool_name,
        policy_effect=payload.policy_effect,
        status=payload.status,
        error_code=_optional_error(payload.error_code),
    )


def _decode_metrics(payload: _ReplayMetricsPayload) -> ReplayMetrics:
    return ReplayMetrics(
        event_count=payload.event_count,
        action_started_count=payload.action_started_count,
        action_completed_count=payload.action_completed_count,
        tool_failure_count=payload.tool_failure_count,
        policy_denial_count=payload.policy_denial_count,
        confirmation_required_count=payload.confirmation_required_count,
        recovery_planned_count=payload.recovery_planned_count,
        recovery_exhausted_count=payload.recovery_exhausted_count,
        human_intervention_count=payload.human_intervention_count,
        resource_lock_acquired_count=payload.resource_lock_acquired_count,
        resource_lock_released_count=payload.resource_lock_released_count,
        resource_conflict_count=payload.resource_conflict_count,
        active_resource_lock_count=payload.active_resource_lock_count,
        decision_generated_count=payload.decision_generated_count,
        decision_validated_count=payload.decision_validated_count,
        decision_rejected_count=payload.decision_rejected_count,
        model_call_count=payload.model_call_count,
        model_total_token_count=payload.model_total_token_count,
        model_estimated_cost_micros=payload.model_estimated_cost_micros,
    )


def _to_json(value: object) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, Mapping):
        return {str(key): _to_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_to_json(item) for item in value]
    return str(value)


def _string_tuple(value: Sequence[str]) -> tuple[str, ...]:
    return tuple(value)


def _optional_error(value: str | None) -> ErrorCode | None:
    if value is None:
        return None
    return ErrorCode(value)


def _parse_recording_payload[T: ConfigPayload](
    model_type: type[T],
    payload: Mapping[str, JsonValue],
) -> T:
    try:
        return model_type.model_validate(dict(payload))
    except PydanticValidationError as exc:
        raise ValueError(_recording_error_message(exc)) from exc


def _recording_error_message(error: PydanticValidationError) -> str:
    errors = error.errors(include_url=False)
    if not errors:
        return str(error)
    first = errors[0]
    path = _pydantic_error_path(first.get("loc", ()))
    error_type = str(first.get("type", ""))
    if error_type == "missing":
        return f"missing required field: {path}"
    expected = _expected_error_type(error_type)
    if expected is not None:
        return f"{path} must be {expected}"
    message = str(first.get("msg", ""))
    if message:
        return message.removeprefix("Value error, ")
    return str(error)


def _pydantic_error_path(location: object) -> str:
    parts: list[str] = []
    if isinstance(location, tuple):
        for item in location:
            if isinstance(item, int):
                if parts:
                    parts[-1] = f"{parts[-1]}[{item}]"
                else:
                    parts.append(f"[{item}]")
            else:
                parts.append(str(item))
    return ".".join(parts)


def _expected_error_type(error_type: str) -> str | None:
    return {
        "bool_type": "a boolean",
        "dict_type": "an object",
        "int_type": "an integer",
        "invalid-json-value": "JSON-compatible",
        "list_type": "a list",
        "model_attributes_type": "an object",
        "model_type": "an object",
        "string_type": "a string",
    }.get(error_type)


def _validate_non_empty_name(field: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{field} must not be empty")


def _validate_tags(field: str, tags: tuple[str, ...]) -> None:
    if any(not tag.strip() for tag in tags):
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
