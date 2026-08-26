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
from universal_agent.runtime import RuntimeEventView


@dataclass(frozen=True, slots=True)
class ReplayedDecision:
    event_id: str
    task_id: str
    decision_type: str
    reason: str


@dataclass(frozen=True, slots=True)
class ReplayedAction:
    action_id: str
    event_types: tuple[str, ...] = ()
    capability: str | None = None
    tool_name: str | None = None
    domain_name: str | None = None
    domain_version: str | None = None
    policy_effect: str | None = None
    policy_name: str | None = None
    status: str | None = None
    error_code: str | None = None
    resource_key: str | None = None
    resource_version: str | None = None
    observation_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    evidence_claims: tuple[str, ...] = ()
    evaluation_status: str | None = None
    evaluator_name: str | None = None
    recovery_strategy: str | None = None
    recovery_rule: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionReplay:
    """Reconstructed execution history from recorded runtime events.

    This is execution replay, not re-execution: it never calls a model, a tool
    or a Domain backend.
    """

    session_id: str
    goal_id: str
    task_ids: tuple[str, ...]
    event_count: int
    event_types: tuple[str, ...]
    decisions: tuple[ReplayedDecision, ...]
    actions: tuple[ReplayedAction, ...]
    observation_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    terminal_status: ExecutionStatus | None
    terminal_error_code: ErrorCode | None
    terminal_reason: str | None


@dataclass(slots=True)
class _ActionReplayBuilder:
    action_id: str
    event_types: list[str] = field(default_factory=list)
    capability: str | None = None
    tool_name: str | None = None
    domain_name: str | None = None
    domain_version: str | None = None
    policy_effect: str | None = None
    policy_name: str | None = None
    status: str | None = None
    error_code: str | None = None
    resource_key: str | None = None
    resource_version: str | None = None
    observation_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    evidence_claims: list[str] = field(default_factory=list)
    evaluation_status: str | None = None
    evaluator_name: str | None = None
    recovery_strategy: str | None = None
    recovery_rule: str | None = None

    def build(self) -> ReplayedAction:
        return ReplayedAction(
            action_id=self.action_id,
            event_types=tuple(self.event_types),
            capability=self.capability,
            tool_name=self.tool_name,
            domain_name=self.domain_name,
            domain_version=self.domain_version,
            policy_effect=self.policy_effect,
            policy_name=self.policy_name,
            status=self.status,
            error_code=self.error_code,
            resource_key=self.resource_key,
            resource_version=self.resource_version,
            observation_ids=tuple(self.observation_ids),
            evidence_ids=tuple(self.evidence_ids),
            evidence_claims=tuple(self.evidence_claims),
            evaluation_status=self.evaluation_status,
            evaluator_name=self.evaluator_name,
            recovery_strategy=self.recovery_strategy,
            recovery_rule=self.recovery_rule,
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
    decision_generated_count: int = 0
    decision_validated_count: int = 0
    decision_rejected_count: int = 0
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

    def __post_init__(self) -> None:
        _validate_non_empty_name("replay recording scenario name", self.scenario_name)


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


def replay_execution(events: tuple[RuntimeEventView, ...]) -> ExecutionReplay:
    if not events:
        raise ValueError("execution replay requires at least one event")
    session_ids = {str(event.session_id) for event in events}
    if len(session_ids) != 1:
        raise ValueError("execution replay requires events from exactly one session")

    first = events[0]
    task_ids: list[str] = []
    observation_ids: list[str] = []
    evidence_ids: list[str] = []
    decisions: list[ReplayedDecision] = []
    actions: dict[str, _ActionReplayBuilder] = {}
    terminal_status: ExecutionStatus | None = None
    terminal_error_code: ErrorCode | None = None
    terminal_reason: str | None = None

    for event in events:
        _append_unique(task_ids, str(event.task_id))
        created_task_id = _string_data(event, "created_task_id")
        if created_task_id is not None:
            _append_unique(task_ids, created_task_id)
        started_task_id = _string_data(event, "started_task_id")
        if started_task_id is not None:
            _append_unique(task_ids, started_task_id)

        if event.type == "DecisionGenerated":
            decisions.append(
                ReplayedDecision(
                    event_id=event.event_id,
                    task_id=str(event.task_id),
                    decision_type=_string_data(event, "decision_type") or "",
                    reason=_string_data(event, "reason") or "",
                )
            )

        if event.action_id is not None:
            action = actions.setdefault(
                str(event.action_id),
                _ActionReplayBuilder(str(event.action_id)),
            )
            _apply_action_event(action, event)

        observation_id = _string_data(event, "observation_id")
        if observation_id is not None:
            _append_unique(observation_ids, observation_id)
        evidence_id = _string_data(event, "evidence_id")
        if evidence_id is not None:
            _append_unique(evidence_ids, evidence_id)

        if event.type == "GoalCompleted":
            terminal_status = ExecutionStatus.COMPLETED
            terminal_error_code = None
            terminal_reason = _string_data(event, "reason")
        elif event.type == "GoalFailed":
            terminal_status = ExecutionStatus.FAILED
            terminal_error_code = _optional_error_code(_string_data(event, "error_code"))
            terminal_reason = _string_data(event, "reason")
        elif event.type == "GoalCancelled":
            terminal_status = ExecutionStatus.CANCELLED
            terminal_error_code = None
            terminal_reason = _string_data(event, "reason")
        elif event.type in {"GoalWaiting", "ConfirmationRequired"}:
            terminal_status = ExecutionStatus.WAITING
            terminal_error_code = None
            terminal_reason = _string_data(event, "reason")

    return ExecutionReplay(
        session_id=str(first.session_id),
        goal_id=str(first.goal_id),
        task_ids=tuple(task_ids),
        event_count=len(events),
        event_types=tuple(event.type for event in events),
        decisions=tuple(decisions),
        actions=tuple(action.build() for action in actions.values()),
        observation_ids=tuple(observation_ids),
        evidence_ids=tuple(evidence_ids),
        terminal_status=terminal_status,
        terminal_error_code=terminal_error_code,
        terminal_reason=terminal_reason,
    )


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
            decision_generated_count=report.metrics.decision_generated_count,
            decision_validated_count=report.metrics.decision_validated_count,
            decision_rejected_count=report.metrics.decision_rejected_count,
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


def _apply_action_event(action: _ActionReplayBuilder, event: RuntimeEventView) -> None:
    action.event_types.append(event.type)
    if event.type in {"CapabilityResolved", "ActionStarted"}:
        action.capability = _string_data(event, "capability") or action.capability
        action.tool_name = _string_data(event, "tool_name") or action.tool_name
        action.domain_name = _string_data(event, "domain") or action.domain_name
        action.domain_version = _string_data(event, "domain_version") or action.domain_version
        action.resource_key = _string_data(event, "resource_key") or action.resource_key
        action.resource_version = _string_data(event, "resource_version") or action.resource_version
    elif event.type == "PolicyChecked":
        action.policy_effect = _string_data(event, "effect") or action.policy_effect
        action.policy_name = _string_data(event, "policy") or action.policy_name
        action.capability = _string_data(event, "capability") or action.capability
        action.tool_name = _string_data(event, "tool_name") or action.tool_name
    elif event.type == "ActionCompleted":
        action.status = _string_data(event, "status") or action.status
        action.error_code = _string_data(event, "error_code") or action.error_code
    elif event.type == "ObservationReceived":
        observation_id = _string_data(event, "observation_id")
        if observation_id is not None:
            _append_unique(action.observation_ids, observation_id)
        action.status = _string_data(event, "status") or action.status
    elif event.type == "EvidenceRecorded":
        evidence_id = _string_data(event, "evidence_id")
        if evidence_id is not None:
            _append_unique(action.evidence_ids, evidence_id)
        claim = _string_data(event, "claim")
        if claim is not None:
            _append_unique(action.evidence_claims, claim)
    elif event.type == "EvaluationCompleted":
        action.evaluation_status = _string_data(event, "status") or action.evaluation_status
        action.evaluator_name = _string_data(event, "evaluator") or action.evaluator_name
    elif event.type in {"RecoveryPlanned", "RecoveryExhausted"}:
        action.recovery_strategy = _string_data(event, "strategy") or action.recovery_strategy
        action.recovery_rule = _string_data(event, "rule") or action.recovery_rule
    elif event.type in {"ResourceLockAcquired", "ResourceLockReleased", "ResourceConflictDetected"}:
        action.resource_key = _string_data(event, "resource_key") or action.resource_key
        action.resource_version = _string_data(event, "resource_version") or action.resource_version
        if event.type == "ResourceConflictDetected":
            action.error_code = ErrorCode.RESOURCE_CONFLICT.value


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


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _string_data(event: RuntimeEventView, key: str) -> str | None:
    value = event.data.get(key)
    if isinstance(value, str):
        return value
    return None


def _optional_error_code(value: str | None) -> ErrorCode | None:
    if value is None:
        return None
    try:
        return ErrorCode(value)
    except ValueError:
        return None


def _validate_non_empty_name(field: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{field} must not be empty")


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
