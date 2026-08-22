from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from universal_agent.core import ErrorCode, ExecutionStatus, immutable_json
from universal_agent.evaluation.recording import (
    EvaluationCheckRecording,
    EvaluationGateRecording,
    EvaluationReportNotFoundError,
    EvaluationReportRecording,
    EvaluationScenarioRecording,
    EvaluationSummaryRecording,
    FileEvaluationReportStore,
    FileReplayRecordingStore,
    ReplayRecordingNotFoundError,
    compare_evaluation_reports,
    decode_evaluation_report,
    decode_replay_recording,
    encode_evaluation_report,
    encode_replay_recording,
)
from universal_agent.evaluation.replay import (
    ReplayAuditEntry,
    ReplayMetrics,
    ReplayRecording,
)


def sample_report_recording(name: str = "nightly behavior suite") -> EvaluationReportRecording:
    return EvaluationReportRecording(
        suite_name=name,
        passed=False,
        summary=EvaluationSummaryRecording(
            scenario_count=2,
            passed_count=1,
            failed_count=1,
            goal_completed_count=1,
            task_completed_count=1,
            action_started_count=1,
            action_completed_count=1,
            tool_failure_count=0,
            policy_denial_count=1,
            recovery_planned_count=0,
            human_intervention_count=0,
            resource_lock_acquired_count=1,
            resource_lock_released_count=1,
            resource_conflict_count=1,
            active_resource_lock_count=0,
            model_call_count=3,
            model_total_token_count=215,
            model_estimated_cost_micros=35,
        ),
        scenarios=(
            EvaluationScenarioRecording(
                scenario_name="healthy workload",
                passed=True,
                result_status=ExecutionStatus.COMPLETED,
                error_code=None,
                satisfied_criteria=immutable_json({"healthy": True}),
                checks=(EvaluationCheckRecording("status", True, "status=completed"),),
                event_types=("GoalCreated", "ActionStarted", "GoalCompleted"),
                action_capabilities=("inspect_workload",),
                audit_capabilities=(),
                metrics=ReplayMetrics(
                    event_count=3,
                    action_started_count=1,
                    action_completed_count=1,
                    tool_failure_count=0,
                    policy_denial_count=0,
                    confirmation_required_count=0,
                    recovery_planned_count=0,
                    recovery_exhausted_count=0,
                    human_intervention_count=0,
                    model_call_count=2,
                    model_total_token_count=125,
                    model_estimated_cost_micros=20,
                ),
            ),
            EvaluationScenarioRecording(
                scenario_name="invalid scale",
                passed=False,
                result_status=ExecutionStatus.FAILED,
                error_code=ErrorCode.POLICY_DENIED,
                checks=(EvaluationCheckRecording("status", False, "expected completed"),),
                event_types=("GoalCreated", "PolicyChecked", "GoalFailed"),
                action_capabilities=(),
                audit_capabilities=("scale_workload",),
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
                    resource_lock_acquired_count=1,
                    resource_lock_released_count=1,
                    resource_conflict_count=1,
                    active_resource_lock_count=0,
                    model_call_count=1,
                    model_total_token_count=90,
                    model_estimated_cost_micros=15,
                ),
            ),
        ),
        gate=EvaluationGateRecording(
            passed=False,
            checks=(
                EvaluationCheckRecording(
                    "pass_rate",
                    False,
                    "expected pass_rate >= 1.000, got 0.500",
                ),
            ),
        ),
    )


def test_evaluation_report_codec_round_trips_stable_report() -> None:
    recording = sample_report_recording()

    restored = decode_evaluation_report(encode_evaluation_report(recording))

    assert restored.suite_name == recording.suite_name
    assert not restored.passed
    assert restored.summary.scenario_count == 2
    assert restored.summary.model_total_token_count == 215
    assert restored.summary.resource_conflict_count == 1
    assert restored.scenarios[0].passed
    assert restored.scenarios[0].satisfied_criteria == {"healthy": True}
    assert restored.scenarios[1].error_code is ErrorCode.POLICY_DENIED
    assert restored.scenarios[1].audit_capabilities == ("scale_workload",)
    assert restored.scenarios[1].metrics.resource_conflict_count == 1
    assert restored.gate is not None
    assert not restored.gate.passed
    assert restored.gate.checks[0].name == "pass_rate"


def test_evaluation_report_codec_decodes_v1_reports_without_gate() -> None:
    payload = encode_evaluation_report(sample_report_recording())
    payload["schema_version"] = 1
    del payload["gate"]

    restored = decode_evaluation_report(payload)

    assert restored.suite_name == "nightly behavior suite"
    assert restored.gate is None


def test_evaluation_report_codec_rejects_unknown_schema_version() -> None:
    payload = encode_evaluation_report(sample_report_recording())
    payload["schema_version"] = 999

    with pytest.raises(ValueError, match="unsupported evaluation report schema version"):
        decode_evaluation_report(payload)


def test_evaluation_report_comparison_passes_matching_recordings() -> None:
    expected = sample_report_recording()
    actual = decode_evaluation_report(encode_evaluation_report(expected))

    comparison = compare_evaluation_reports(expected, actual)

    assert comparison.passed
    assert comparison.failed_checks == ()


def test_evaluation_report_comparison_detects_behavior_drift() -> None:
    expected = sample_report_recording()
    assert expected.gate is not None
    drifted_summary = replace(expected.summary, action_started_count=2)
    drifted_healthy = replace(
        expected.scenarios[0],
        action_capabilities=("query_metrics",),
    )
    drifted_gate = replace(
        expected.gate,
        checks=(EvaluationCheckRecording("pass_rate", True, "matched"),),
    )
    actual = replace(
        expected,
        summary=drifted_summary,
        scenarios=(drifted_healthy, expected.scenarios[1]),
        gate=drifted_gate,
    )

    comparison = compare_evaluation_reports(expected, actual)

    assert not comparison.passed
    assert {
        "summary",
        "scenario:healthy workload:action_capabilities",
        "gate:checks",
    } <= {check.name for check in comparison.failed_checks}


def test_file_evaluation_report_store_saves_lists_and_loads_reports(tmp_path: Path) -> None:
    store = FileEvaluationReportStore(tmp_path)
    first = sample_report_recording("nightly behavior suite")
    second = sample_report_recording("policy regression suite")

    store.save(first)
    store.save(second)

    assert [item.suite_name for item in store.list_reports()] == [
        "nightly behavior suite",
        "policy regression suite",
    ]
    assert store.load("policy regression suite").scenarios[1].audit_capabilities == (
        "scale_workload",
    )


def test_file_evaluation_report_store_reports_missing_report(tmp_path: Path) -> None:
    store = FileEvaluationReportStore(tmp_path)

    with pytest.raises(EvaluationReportNotFoundError, match="missing suite"):
        store.load("missing suite")


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
            resource_lock_acquired_count=1,
            resource_lock_released_count=1,
            resource_conflict_count=1,
            active_resource_lock_count=0,
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
