from __future__ import annotations

from collections.abc import Mapping

from pydantic import Field

from universal_agent.core import (
    CapabilitySummary,
    ContextFragment,
    Decision,
    DecisionContext,
    DecisionType,
    JsonCodecError,
    JsonMapping,
    JsonValue,
    Observation,
    SuccessCriterion,
    immutable_json,
    validate_argument_contract,
)
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    parse_json_object,
    parse_non_empty_string,
    parse_non_empty_string_sequence,
    parse_optional_non_empty_string,
    parse_payload,
)
from universal_agent.model.adapter import ModelUsage
from universal_agent.model.errors import JsonHttpModelError


class _DecisionPayload(ConfigPayload):
    type: str
    reason: str
    capability: str | None = None
    target: str | None = None
    arguments: dict[str, PydanticJsonValue] = Field(default_factory=dict)
    expected_observations: list[str] = Field(default_factory=list)
    message: str | None = None


class _UsagePayload(ConfigPayload):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_micros: int = Field(default=0, ge=0)
    currency: str | None = None


MODEL_PAYLOAD_EXPECTED_TYPES = {"greater_than_equal": "a non-negative integer"}


def decision_context_payload(context: DecisionContext) -> JsonMapping:
    payload: dict[str, JsonValue] = {
        "session_id": str(context.session_id),
        "goal_id": str(context.goal_id),
        "goal_description": context.goal_description,
        "goal_success_criteria": [
            _success_criterion_payload(item) for item in context.goal_success_criteria
        ],
        "task_id": str(context.task_id),
        "task_description": context.task_description,
        "current_task_required_criteria": list(context.current_task_required_criteria),
        "iteration": context.iteration,
        "satisfied_criteria": dict(context.satisfied_criteria),
        "latest_observation": _observation_payload(context.latest_observation),
        "capabilities": [_capability_payload(item) for item in context.capabilities],
        "domain_context": _fragment_payloads(context.domain_context),
        "world_context": _fragment_payloads(context.world_context),
        "evidence_context": _fragment_payloads(context.evidence_context),
        "task_context": _fragment_payloads(context.task_context),
        "memory_context": _fragment_payloads(context.memory_context),
        "policy_summary": list(context.policy_summary),
    }
    return immutable_json(payload)


def decision_payload(response: JsonMapping) -> JsonMapping:
    raw_decision = response.get("decision", response)
    try:
        return json_mapping(raw_decision, "decision")
    except JsonHttpModelError as exc:
        raise JsonHttpModelError("model response decision must be an object") from exc


def decode_decision(payload: JsonMapping) -> Decision:
    parsed = parse_value_payload(_DecisionPayload, payload)
    decision_type = DecisionType(model_non_empty_string(parsed.type, "type"))
    reason = model_non_empty_string(parsed.reason, "reason")
    capability = model_optional_non_empty_string(parsed.capability, "capability")
    target = model_optional_non_empty_string(parsed.target, "target")
    arguments = immutable_json(parsed.arguments)
    expected_observations = parse_non_empty_string_sequence(
        parsed.expected_observations,
        "expected_observations",
        empty_template="{path} must be a non-empty string",
    )
    message = model_optional_non_empty_string(parsed.message, "message")
    return Decision(
        decision_type,
        reason,
        capability=capability,
        target=target,
        arguments=arguments,
        expected_observations=expected_observations,
        message=message,
    )


def validate_decision_against_context(decision: Decision, context: DecisionContext) -> None:
    if decision.type is not DecisionType.EXECUTE:
        return
    capability = decision.capability or ""
    available = {item.name: item for item in context.capabilities}
    summary = available.get(capability)
    if summary is None:
        raise ValueError(f"capability is not available in context: {capability}")
    argument_error = validate_argument_contract(
        required_arguments=summary.required_arguments,
        argument_schema=summary.argument_schema,
        arguments=decision.arguments,
    )
    if argument_error is not None:
        raise ValueError(f"arguments for capability {capability}: {argument_error}")


def decode_usage(provider: str, model: str, value: JsonValue) -> ModelUsage | None:
    if value is None:
        return None
    try:
        usage_payload = json_mapping(value, "usage")
    except JsonHttpModelError as exc:
        raise JsonHttpModelError("model usage must be an object") from exc
    usage = parse_model_payload(_UsagePayload, usage_payload, "usage")
    input_tokens = usage.input_tokens if usage.input_tokens is not None else usage.prompt_tokens
    output_tokens = (
        usage.output_tokens if usage.output_tokens is not None else usage.completion_tokens
    )
    currency = model_optional_non_empty_string(usage.currency, "usage.currency") or "USD"
    return ModelUsage(
        provider,
        model,
        input_tokens=input_tokens or 0,
        output_tokens=output_tokens or 0,
        estimated_cost_micros=usage.estimated_cost_micros,
        currency=currency,
    )


def json_error_message(error: JsonCodecError) -> str:
    return str(error).removeprefix("invalid JSON: ")


def json_mapping(value: object, field_name: str) -> JsonMapping:
    candidate = dict(value) if isinstance(value, Mapping) else value
    try:
        return immutable_json(parse_json_object(candidate, field_name))
    except ValueError as exc:
        raise JsonHttpModelError(str(exc)) from exc


def model_non_empty_string(value: str, field_name: str) -> str:
    return parse_non_empty_string(
        value,
        field_name,
        empty_template="{path} must be a non-empty string",
    )


def model_optional_non_empty_string(value: str | None, field_name: str) -> str | None:
    return parse_optional_non_empty_string(
        value,
        field_name,
        empty_template="{path} must be a non-empty string",
    )


def validate_headers(headers: Mapping[str, str]) -> None:
    for name, value in headers.items():
        parse_non_empty_string(name, "model extra header name")
        if "\n" in name or "\r" in name or "\n" in value or "\r" in value:
            raise ValueError("model extra headers must not contain newlines")


def parse_value_payload[T: ConfigPayload](
    model_type: type[T],
    payload: Mapping[str, JsonValue],
) -> T:
    return parse_payload(
        model_type,
        payload,
        missing_template="{path} is required",
        expected_types=MODEL_PAYLOAD_EXPECTED_TYPES,
    )


def parse_model_payload[T: ConfigPayload](
    model_type: type[T],
    payload: Mapping[str, JsonValue],
    field_name: str,
) -> T:
    try:
        return parse_payload(
            model_type,
            payload,
            field=field_name,
            missing_template="{path} is required",
            expected_types=MODEL_PAYLOAD_EXPECTED_TYPES,
        )
    except ValueError as exc:
        raise JsonHttpModelError(str(exc)) from exc


def _success_criterion_payload(criterion: SuccessCriterion) -> dict[str, JsonValue]:
    return {"key": criterion.key, "expected": criterion.expected}


def _observation_payload(observation: Observation | None) -> dict[str, JsonValue] | None:
    if observation is None:
        return None
    return {
        "observation_id": str(observation.id),
        "action_id": str(observation.action_id),
        "task_id": str(observation.task_id),
        "source": observation.source,
        "status": observation.status.value,
        "data": dict(observation.data),
        "observed_at": observation.observed_at.isoformat(),
        "error": observation.error,
        "error_code": None if observation.error_code is None else observation.error_code.value,
    }


def _capability_payload(capability: CapabilitySummary) -> dict[str, JsonValue]:
    return {
        "name": capability.name,
        "description": capability.description,
        "category": capability.category.value,
        "risk": capability.risk.value,
        "required_arguments": list(capability.required_arguments),
        "argument_schema": dict(capability.argument_schema),
    }


def _fragment_payloads(fragments: tuple[ContextFragment, ...]) -> list[JsonValue]:
    return [
        {"key": item.key, "content": item.content, "priority": item.priority} for item in fragments
    ]
