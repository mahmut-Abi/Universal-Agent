from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from universal_agent.core import ErrorCode, ExecutionStatus, JsonMapping, JsonValue, immutable_json
from universal_agent.evaluation.replay import ReplayAuditEntry, ReplayMetrics, ReplayRecording

REPLAY_RECORDING_SCHEMA_VERSION = 1
JsonObject = dict[str, JsonValue]


class ReplayRecordingNotFoundError(LookupError):
    pass


class ReplayRecordingStore(Protocol):
    def save(self, recording: ReplayRecording) -> None: ...

    def load(self, scenario_name: str) -> ReplayRecording: ...

    def list_recordings(self) -> tuple[ReplayRecording, ...]: ...


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
        "metrics": {
            "event_count": recording.metrics.event_count,
            "action_started_count": recording.metrics.action_started_count,
            "action_completed_count": recording.metrics.action_completed_count,
            "tool_failure_count": recording.metrics.tool_failure_count,
            "policy_denial_count": recording.metrics.policy_denial_count,
            "confirmation_required_count": recording.metrics.confirmation_required_count,
            "recovery_planned_count": recording.metrics.recovery_planned_count,
            "recovery_exhausted_count": recording.metrics.recovery_exhausted_count,
            "human_intervention_count": recording.metrics.human_intervention_count,
            "model_call_count": recording.metrics.model_call_count,
            "model_total_token_count": recording.metrics.model_total_token_count,
            "model_estimated_cost_micros": recording.metrics.model_estimated_cost_micros,
        },
    }


def decode_replay_recording(payload: Mapping[str, JsonValue]) -> ReplayRecording:
    version = _int(_required(payload, "schema_version"), "schema_version")
    if version != REPLAY_RECORDING_SCHEMA_VERSION:
        raise ValueError(f"unsupported replay recording schema version: {version}")
    return ReplayRecording(
        scenario_name=_string(_required(payload, "scenario_name"), "scenario_name"),
        result_status=ExecutionStatus(
            _string(_required(payload, "result_status"), "result_status")
        ),
        error_code=_optional_error(_required(payload, "error_code")),
        satisfied_criteria=immutable_json(
            _object(_required(payload, "satisfied_criteria"), "satisfied_criteria")
        ),
        event_types=_string_tuple(_required(payload, "event_types"), "event_types"),
        action_capabilities=_string_tuple(
            _required(payload, "action_capabilities"),
            "action_capabilities",
        ),
        action_statuses=_string_tuple(_required(payload, "action_statuses"), "action_statuses"),
        policy_effects=_string_tuple(_required(payload, "policy_effects"), "policy_effects"),
        audit_entries=tuple(
            _decode_audit_entry(_object(item, "audit_entries[]"))
            for item in _list(_required(payload, "audit_entries"), "audit_entries")
        ),
        metrics=_decode_metrics(_object(_required(payload, "metrics"), "metrics")),
    )


def json_mapping(value: object) -> JsonMapping:
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return value
    raise ValueError("expected a JSON object")


def _decode_audit_entry(payload: JsonObject) -> ReplayAuditEntry:
    return ReplayAuditEntry(
        capability=_string(_required(payload, "capability"), "audit_entry.capability"),
        tool_name=_string(_required(payload, "tool_name"), "audit_entry.tool_name"),
        policy_effect=_string(_required(payload, "policy_effect"), "audit_entry.policy_effect"),
        status=_string(_required(payload, "status"), "audit_entry.status"),
        error_code=_optional_error(_required(payload, "error_code")),
    )


def _decode_metrics(payload: JsonObject) -> ReplayMetrics:
    return ReplayMetrics(
        event_count=_int(_required(payload, "event_count"), "metrics.event_count"),
        action_started_count=_int(
            _required(payload, "action_started_count"),
            "metrics.action_started_count",
        ),
        action_completed_count=_int(
            _required(payload, "action_completed_count"),
            "metrics.action_completed_count",
        ),
        tool_failure_count=_int(
            _required(payload, "tool_failure_count"),
            "metrics.tool_failure_count",
        ),
        policy_denial_count=_int(
            _required(payload, "policy_denial_count"),
            "metrics.policy_denial_count",
        ),
        confirmation_required_count=_int(
            _required(payload, "confirmation_required_count"),
            "metrics.confirmation_required_count",
        ),
        recovery_planned_count=_int(
            _required(payload, "recovery_planned_count"),
            "metrics.recovery_planned_count",
        ),
        recovery_exhausted_count=_int(
            _required(payload, "recovery_exhausted_count"),
            "metrics.recovery_exhausted_count",
        ),
        human_intervention_count=_int(
            _required(payload, "human_intervention_count"),
            "metrics.human_intervention_count",
        ),
        model_call_count=_optional_int(
            payload,
            "model_call_count",
            "metrics.model_call_count",
        ),
        model_total_token_count=_optional_int(
            payload,
            "model_total_token_count",
            "metrics.model_total_token_count",
        ),
        model_estimated_cost_micros=_optional_int(
            payload,
            "model_estimated_cost_micros",
            "metrics.model_estimated_cost_micros",
        ),
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


def _required(payload: Mapping[str, JsonValue], key: str) -> JsonValue:
    try:
        return payload[key]
    except KeyError as exc:
        raise ValueError(f"missing required field: {key}") from exc


def _object(value: JsonValue, field: str) -> JsonObject:
    if isinstance(value, dict):
        return value
    raise ValueError(f"{field} must be an object")


def _list(value: JsonValue, field: str) -> list[JsonValue]:
    if isinstance(value, list):
        return value
    raise ValueError(f"{field} must be a list")


def _string(value: JsonValue, field: str) -> str:
    if isinstance(value, str):
        return value
    raise ValueError(f"{field} must be a string")


def _string_tuple(value: JsonValue, field: str) -> tuple[str, ...]:
    return tuple(_string(item, f"{field}[]") for item in _list(value, field))


def _int(value: JsonValue, field: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError(f"{field} must be an integer")


def _optional_int(payload: Mapping[str, JsonValue], key: str, field: str) -> int:
    value = payload.get(key)
    if value is None:
        return 0
    return _int(value, field)


def _optional_error(value: JsonValue) -> ErrorCode | None:
    if value is None:
        return None
    return ErrorCode(_string(value, "error_code"))
