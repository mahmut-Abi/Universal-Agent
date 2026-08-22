from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent.core import ErrorCode, ExecutionStatus, immutable_json
from universal_agent.evaluation.recording import (
    FileReplayRecordingStore,
    ReplayRecordingNotFoundError,
    decode_replay_recording,
    encode_replay_recording,
)
from universal_agent.evaluation.replay import (
    ReplayAuditEntry,
    ReplayMetrics,
    ReplayRecording,
)


def sample_recording(name: str = "policy regression") -> ReplayRecording:
    return ReplayRecording(
        scenario_name=name,
        result_status=ExecutionStatus.FAILED,
        error_code=ErrorCode.POLICY_DENIED,
        satisfied_criteria=immutable_json({"healthy": False, "replicas": 0}),
        event_types=("DomainActivated", "PolicyChecked", "GoalFailed"),
        action_capabilities=(),
        action_statuses=(),
        policy_effects=("deny",),
        audit_entries=(
            ReplayAuditEntry(
                "scale_workload",
                "kubernetes_scale_workload",
                "deny",
                "denied",
                None,
            ),
        ),
        metrics=ReplayMetrics(
            event_count=3,
            action_started_count=0,
            action_completed_count=0,
            tool_failure_count=0,
            policy_denial_count=1,
            confirmation_required_count=0,
            recovery_planned_count=0,
            recovery_exhausted_count=0,
            human_intervention_count=0,
            model_call_count=2,
            model_total_token_count=150,
            model_estimated_cost_micros=25,
        ),
    )


def test_replay_recording_codec_round_trips_stable_trace() -> None:
    recording = sample_recording()

    restored = decode_replay_recording(encode_replay_recording(recording))

    assert restored.scenario_name == recording.scenario_name
    assert restored.result_status is ExecutionStatus.FAILED
    assert restored.error_code is ErrorCode.POLICY_DENIED
    assert restored.satisfied_criteria == recording.satisfied_criteria
    assert restored.event_types == recording.event_types
    assert restored.audit_entries == recording.audit_entries
    assert restored.metrics == recording.metrics


def test_replay_recording_codec_defaults_missing_model_metrics() -> None:
    payload = encode_replay_recording(sample_recording())
    metrics = payload["metrics"]
    assert isinstance(metrics, dict)
    del metrics["model_call_count"]
    del metrics["model_total_token_count"]
    del metrics["model_estimated_cost_micros"]

    restored = decode_replay_recording(payload)

    assert restored.metrics.model_call_count == 0
    assert restored.metrics.model_total_token_count == 0
    assert restored.metrics.model_estimated_cost_micros == 0


def test_replay_recording_codec_rejects_unknown_schema_version() -> None:
    payload = encode_replay_recording(sample_recording())
    payload["schema_version"] = 999

    with pytest.raises(ValueError, match="unsupported replay recording schema version"):
        decode_replay_recording(payload)


def test_file_replay_recording_store_saves_lists_and_loads_recordings(tmp_path: Path) -> None:
    store = FileReplayRecordingStore(tmp_path)
    first = sample_recording("policy regression")
    second = sample_recording("healthy replay")

    store.save(first)
    store.save(second)

    assert [item.scenario_name for item in store.list_recordings()] == [
        "healthy replay",
        "policy regression",
    ]
    assert store.load("policy regression").audit_entries == first.audit_entries


def test_file_replay_recording_store_reports_missing_recording(tmp_path: Path) -> None:
    store = FileReplayRecordingStore(tmp_path)

    with pytest.raises(ReplayRecordingNotFoundError, match="missing replay"):
        store.load("missing replay")
