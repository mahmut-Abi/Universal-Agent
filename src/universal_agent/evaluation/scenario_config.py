from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError

from universal_agent.core import (
    ErrorCode,
    ExecutionStatus,
    Goal,
    JsonValue,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    json_mapping,
    parse_json_object,
)
from universal_agent.evaluation.harness import (
    EvaluationQualityGate,
    EvaluationScenario,
    EvaluationScenarioKind,
    EvaluationSuite,
    ScenarioExpectations,
)


@dataclass(frozen=True, slots=True)
class EvaluationSuiteConfig:
    suite: EvaluationSuite
    quality_gate: EvaluationQualityGate | None = None


class _SuccessCriterionPayload(ConfigPayload):
    key: str
    expected: PydanticJsonValue


class _GoalPayload(ConfigPayload):
    description: str
    success_criteria: dict[str, PydanticJsonValue] | list[_SuccessCriterionPayload] = Field(
        default_factory=dict
    )


class _TaskPayload(ConfigPayload):
    description: str
    required_criteria: list[str]


class _ExpectationsPayload(ConfigPayload):
    expected_status: str = ExecutionStatus.COMPLETED.value
    expected_error_code: str | None = None
    expected_criteria: dict[str, PydanticJsonValue] = Field(default_factory=dict)
    required_events: list[str] = Field(default_factory=list)
    forbidden_events: list[str] = Field(default_factory=list)
    required_evidence_claims: list[str] = Field(default_factory=list)
    forbidden_evidence_claims: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    allowed_capabilities: list[str] | None = None
    required_audit_capabilities: list[str] = Field(default_factory=list)
    decision_rejected_count: int | None = None
    policy_denial_count: int | None = None
    recovery_planned_count: int | None = None
    resource_conflict_count: int | None = None
    active_resource_lock_count: int | None = None
    max_actions: int | None = None
    max_iterations: int | None = None
    max_execution_duration_ms: int | None = None
    max_model_total_tokens: int | None = None
    max_model_estimated_cost_micros: int | None = None


class _ScenarioPayload(ConfigPayload):
    name: str
    goal: _GoalPayload
    task: _TaskPayload
    expectations: _ExpectationsPayload | None = None
    kind: str = EvaluationScenarioKind.SCENARIO.value
    tags: list[str] = Field(default_factory=list)


class _QualityGatePayload(ConfigPayload):
    min_pass_rate: float | None = None
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


class _EvaluationSuitePayload(ConfigPayload):
    name: str
    scenarios: list[_ScenarioPayload]
    tags: list[str] = Field(default_factory=list)
    quality_gate: _QualityGatePayload | None = None


def load_evaluation_suite(path: str | Path) -> EvaluationSuite:
    return load_evaluation_suite_config(path).suite


def load_evaluation_suite_config(path: str | Path) -> EvaluationSuiteConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        loaded: object = json.load(handle)
    return evaluation_suite_config_from_mapping(parse_json_object(loaded, "evaluation suite file"))


def evaluation_suite_config_from_mapping(values: Mapping[str, JsonValue]) -> EvaluationSuiteConfig:
    payload = _parse_config_payload(_EvaluationSuitePayload, values)
    return EvaluationSuiteConfig(
        _suite_from_payload(payload),
        None if payload.quality_gate is None else _quality_gate_from_payload(payload.quality_gate),
    )


def evaluation_suite_from_mapping(values: Mapping[str, JsonValue]) -> EvaluationSuite:
    return _suite_from_payload(_parse_config_payload(_EvaluationSuitePayload, values))


def _suite_from_payload(payload: _EvaluationSuitePayload) -> EvaluationSuite:
    return EvaluationSuite(
        payload.name,
        tuple(_scenario_from_payload(item) for item in payload.scenarios),
        tags=_string_tuple(payload.tags, "tags"),
    )


def _scenario_from_payload(payload: _ScenarioPayload) -> EvaluationScenario:
    return EvaluationScenario(
        payload.name,
        _goal_from_payload(payload.goal),
        _task_from_payload(payload.task),
        _expectations_from_payload(payload.expectations)
        if payload.expectations is not None
        else ScenarioExpectations(),
        kind=EvaluationScenarioKind(payload.kind),
        tags=_string_tuple(payload.tags, "scenario.tags"),
    )


def _goal_from_payload(payload: _GoalPayload) -> Goal:
    return Goal(
        payload.description,
        _success_criteria(payload.success_criteria),
    )


def _task_from_payload(payload: _TaskPayload) -> Task:
    return Task(
        payload.description,
        _string_tuple(payload.required_criteria, "task.required_criteria"),
    )


def _expectations_from_payload(payload: _ExpectationsPayload) -> ScenarioExpectations:
    return ScenarioExpectations(
        expected_status=ExecutionStatus(payload.expected_status),
        expected_error_code=_optional_error_code(payload.expected_error_code),
        expected_criteria=immutable_json(json_mapping(payload.expected_criteria)),
        required_events=_string_tuple(payload.required_events, "expectations.required_events"),
        forbidden_events=_string_tuple(payload.forbidden_events, "expectations.forbidden_events"),
        required_evidence_claims=_string_tuple(
            payload.required_evidence_claims,
            "expectations.required_evidence_claims",
        ),
        forbidden_evidence_claims=_string_tuple(
            payload.forbidden_evidence_claims,
            "expectations.forbidden_evidence_claims",
        ),
        required_capabilities=_string_tuple(
            payload.required_capabilities,
            "expectations.required_capabilities",
        ),
        allowed_capabilities=_optional_string_tuple(
            payload.allowed_capabilities,
            "expectations.allowed_capabilities",
        ),
        required_audit_capabilities=_string_tuple(
            payload.required_audit_capabilities,
            "expectations.required_audit_capabilities",
        ),
        decision_rejected_count=payload.decision_rejected_count,
        policy_denial_count=payload.policy_denial_count,
        recovery_planned_count=payload.recovery_planned_count,
        resource_conflict_count=payload.resource_conflict_count,
        active_resource_lock_count=payload.active_resource_lock_count,
        max_actions=payload.max_actions,
        max_iterations=payload.max_iterations,
        max_execution_duration_ms=payload.max_execution_duration_ms,
        max_model_total_tokens=payload.max_model_total_tokens,
        max_model_estimated_cost_micros=payload.max_model_estimated_cost_micros,
    )


def _quality_gate_from_payload(payload: _QualityGatePayload) -> EvaluationQualityGate:
    return EvaluationQualityGate(
        min_pass_rate=1.0 if payload.min_pass_rate is None else payload.min_pass_rate,
        min_goal_completion_rate=payload.min_goal_completion_rate,
        min_task_success_rate=payload.min_task_success_rate,
        min_action_success_rate=payload.min_action_success_rate,
        max_tool_failure_rate=payload.max_tool_failure_rate,
        max_policy_denial_rate=payload.max_policy_denial_rate,
        max_average_recoveries_per_scenario=payload.max_average_recoveries_per_scenario,
        max_human_intervention_rate=payload.max_human_intervention_rate,
        max_resource_conflict_rate=payload.max_resource_conflict_rate,
        max_average_active_resource_locks_per_scenario=(
            payload.max_average_active_resource_locks_per_scenario
        ),
        max_average_actions_per_scenario=payload.max_average_actions_per_scenario,
        max_average_execution_duration_ms_per_scenario=(
            payload.max_average_execution_duration_ms_per_scenario
        ),
        max_average_model_calls_per_scenario=payload.max_average_model_calls_per_scenario,
        max_average_model_tokens_per_scenario=payload.max_average_model_tokens_per_scenario,
        max_total_model_estimated_cost_micros=payload.max_total_model_estimated_cost_micros,
    )


def _success_criteria(
    value: dict[str, PydanticJsonValue] | list[_SuccessCriterionPayload],
) -> tuple[SuccessCriterion, ...]:
    if isinstance(value, dict):
        return tuple(SuccessCriterion(key, item) for key, item in value.items())
    return tuple(
        SuccessCriterion(
            item.key,
            item.expected,
        )
        for item in value
    )


def _optional_error_code(value: str | None) -> ErrorCode | None:
    if value is None:
        return None
    return ErrorCode(value)


def _optional_string_tuple(value: Sequence[str] | None, field: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    return _string_tuple(value, field)


def _string_tuple(value: Sequence[str], field: str) -> tuple[str, ...]:
    return tuple(value)


def _parse_config_payload[T: ConfigPayload](
    model_type: type[T],
    values: Mapping[str, JsonValue],
) -> T:
    try:
        return model_type.model_validate(dict(values))
    except PydanticValidationError as exc:
        raise ValueError(_scenario_config_error_message(exc)) from exc


def _scenario_config_error_message(error: PydanticValidationError) -> str:
    errors = error.errors(include_url=False)
    if not errors:
        return str(error)
    first = errors[0]
    path = _pydantic_error_path(first.get("loc", ()))
    error_type = str(first.get("type", ""))
    if error_type == "missing":
        return f"{path} is required"
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
        "float_type": "a number",
        "int_type": "an integer",
        "invalid-json-value": "JSON-compatible",
        "list_type": "a list",
        "string_type": "a string",
    }.get(error_type)


__all__ = [
    "EvaluationSuiteConfig",
    "evaluation_suite_config_from_mapping",
    "evaluation_suite_from_mapping",
    "load_evaluation_suite",
    "load_evaluation_suite_config",
]
