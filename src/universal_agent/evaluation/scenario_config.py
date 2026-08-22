from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from universal_agent.core import (
    ErrorCode,
    ExecutionStatus,
    Goal,
    JsonValue,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.evaluation.harness import (
    EvaluationScenario,
    EvaluationScenarioKind,
    EvaluationSuite,
    ScenarioExpectations,
)


def load_evaluation_suite(path: str | Path) -> EvaluationSuite:
    with Path(path).open("r", encoding="utf-8") as handle:
        loaded: object = json.load(handle)
    return evaluation_suite_from_mapping(
        _object(_json_value(loaded, "evaluation suite file"), "evaluation suite file")
    )


def evaluation_suite_from_mapping(values: Mapping[str, JsonValue]) -> EvaluationSuite:
    return EvaluationSuite(
        _string(_required(values, "name"), "name"),
        tuple(
            _scenario_from_mapping(_object(item, "scenarios[]"))
            for item in _list(_required(values, "scenarios"), "scenarios")
        ),
        tags=_string_tuple(values.get("tags", []), "tags"),
    )


def _scenario_from_mapping(values: Mapping[str, JsonValue]) -> EvaluationScenario:
    goal = _goal_from_mapping(_object(_required(values, "goal"), "goal"))
    task = _task_from_mapping(_object(_required(values, "task"), "task"))
    expectations = values.get("expectations")
    return EvaluationScenario(
        _string(_required(values, "name"), "scenario.name"),
        goal,
        task,
        _expectations_from_mapping(_object(expectations, "expectations"))
        if expectations is not None
        else ScenarioExpectations(),
        kind=EvaluationScenarioKind(
            _string(values.get("kind", EvaluationScenarioKind.SCENARIO.value), "scenario.kind")
        ),
        tags=_string_tuple(values.get("tags", []), "scenario.tags"),
    )


def _goal_from_mapping(values: Mapping[str, JsonValue]) -> Goal:
    return Goal(
        _string(_required(values, "description"), "goal.description"),
        _success_criteria(values.get("success_criteria", {})),
    )


def _task_from_mapping(values: Mapping[str, JsonValue]) -> Task:
    return Task(
        _string(_required(values, "description"), "task.description"),
        _string_tuple(_required(values, "required_criteria"), "task.required_criteria"),
    )


def _expectations_from_mapping(values: Mapping[str, JsonValue]) -> ScenarioExpectations:
    return ScenarioExpectations(
        expected_status=ExecutionStatus(
            _string(
                values.get("expected_status", ExecutionStatus.COMPLETED.value),
                "expectations.expected_status",
            )
        ),
        expected_error_code=_optional_error_code(values.get("expected_error_code")),
        expected_criteria=immutable_json(
            _object(values.get("expected_criteria", {}), "expectations.expected_criteria")
        ),
        required_events=_string_tuple(
            values.get("required_events", []), "expectations.required_events"
        ),
        forbidden_events=_string_tuple(
            values.get("forbidden_events", []), "expectations.forbidden_events"
        ),
        required_evidence_claims=_string_tuple(
            values.get("required_evidence_claims", []),
            "expectations.required_evidence_claims",
        ),
        forbidden_evidence_claims=_string_tuple(
            values.get("forbidden_evidence_claims", []),
            "expectations.forbidden_evidence_claims",
        ),
        required_capabilities=_string_tuple(
            values.get("required_capabilities", []),
            "expectations.required_capabilities",
        ),
        allowed_capabilities=_optional_string_tuple(
            values.get("allowed_capabilities"),
            "expectations.allowed_capabilities",
        ),
        required_audit_capabilities=_string_tuple(
            values.get("required_audit_capabilities", []),
            "expectations.required_audit_capabilities",
        ),
        policy_denial_count=_optional_int(
            values.get("policy_denial_count"), "expectations.policy_denial_count"
        ),
        recovery_planned_count=_optional_int(
            values.get("recovery_planned_count"),
            "expectations.recovery_planned_count",
        ),
        resource_conflict_count=_optional_int(
            values.get("resource_conflict_count"),
            "expectations.resource_conflict_count",
        ),
        active_resource_lock_count=_optional_int(
            values.get("active_resource_lock_count"),
            "expectations.active_resource_lock_count",
        ),
        max_actions=_optional_int(values.get("max_actions"), "expectations.max_actions"),
        max_iterations=_optional_int(values.get("max_iterations"), "expectations.max_iterations"),
        max_execution_duration_ms=_optional_int(
            values.get("max_execution_duration_ms"),
            "expectations.max_execution_duration_ms",
        ),
        max_model_total_tokens=_optional_int(
            values.get("max_model_total_tokens"),
            "expectations.max_model_total_tokens",
        ),
        max_model_estimated_cost_micros=_optional_int(
            values.get("max_model_estimated_cost_micros"),
            "expectations.max_model_estimated_cost_micros",
        ),
    )


def _success_criteria(value: JsonValue) -> tuple[SuccessCriterion, ...]:
    if isinstance(value, dict):
        return tuple(
            SuccessCriterion(_string(key, "goal.success_criteria.key"), item)
            for key, item in value.items()
        )
    return tuple(
        SuccessCriterion(
            _string(
                _required(_object(item, "goal.success_criteria[]"), "key"),
                "goal.success_criteria[].key",
            ),
            _required(_object(item, "goal.success_criteria[]"), "expected"),
        )
        for item in _list(value, "goal.success_criteria")
    )


def _optional_error_code(value: JsonValue) -> ErrorCode | None:
    if value is None:
        return None
    return ErrorCode(_string(value, "expectations.expected_error_code"))


def _optional_int(value: JsonValue, field: str) -> int | None:
    if value is None:
        return None
    return _int(value, field)


def _optional_string_tuple(value: JsonValue, field: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    return _string_tuple(value, field)


def _required(values: Mapping[str, JsonValue], key: str) -> JsonValue:
    if key not in values:
        raise ValueError(f"{key} is required")
    return values[key]


def _object(value: JsonValue, field: str) -> Mapping[str, JsonValue]:
    if isinstance(value, dict):
        return value
    raise ValueError(f"{field} must be an object")


def _list(value: JsonValue, field: str) -> list[JsonValue]:
    if isinstance(value, list):
        return value
    raise ValueError(f"{field} must be a list")


def _string_tuple(value: JsonValue, field: str) -> tuple[str, ...]:
    return tuple(_string(item, f"{field}[]") for item in _list(value, field))


def _string(value: JsonValue, field: str) -> str:
    if isinstance(value, str):
        return value
    raise ValueError(f"{field} must be a string")


def _int(value: JsonValue, field: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError(f"{field} must be an integer")


def _json_value(value: object, field: str) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list):
        return [_json_value(item, f"{field}[]") for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _json_value(item, f"{field}.{key}") for key, item in value.items()}
    raise ValueError(f"{field} must be JSON-compatible")


__all__ = ["evaluation_suite_from_mapping", "load_evaluation_suite"]
