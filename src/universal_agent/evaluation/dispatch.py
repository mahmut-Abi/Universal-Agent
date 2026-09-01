from __future__ import annotations

import argparse
from pathlib import Path
from typing import TextIO, cast

from universal_agent.core import (
    DomainIdentity,
    ErrorCode,
    ExecutionStatus,
    Goal,
    SuccessCriterion,
    Task,
    immutable_json,
    read_json_file,
    to_json_object,
    to_json_value,
)
from universal_agent.evaluation.console import (
    build_evaluation_console_snapshot,
    render_evaluation_console,
    render_evaluation_console_text,
)
from universal_agent.evaluation.dataset import (
    EvaluationDataset,
    EvaluationDatasetIdentity,
    EvaluationDatasetRegistry,
    EvaluationDatasetVerificationReport,
)
from universal_agent.evaluation.harness import (
    EvaluationQualityGate,
    EvaluationScenario,
    EvaluationScenarioKind,
    EvaluationScenarioSelector,
    EvaluationSuite,
    ScenarioExpectations,
)
from universal_agent.evaluation.recording import (
    EvaluationCheckRecording,
    EvaluationGateRecording,
    EvaluationReportComparison,
    EvaluationReportComparisonCheck,
    EvaluationReportRecording,
    EvaluationScenarioRecording,
    EvaluationSummaryRecording,
    FileEvaluationReportStore,
    FileReplayRecordingStore,
    ReplayRecordingNotFoundError,
    compare_evaluation_reports,
    decode_evaluation_report,
    encode_evaluation_junit_xml,
    encode_replay_recording,
    json_mapping,
)
from universal_agent.evaluation.replay import (
    DeterministicReplayHarness,
    ReplayCheck,
    ReplayRecording,
    ReplayReport,
)
from universal_agent.evaluation.runner import EvaluationRunner, EvaluationRunResult
from universal_agent.evaluation.scenario_config import (
    EvaluationSuiteConfig,
    load_evaluation_suite_config,
)
from universal_agent.service import RuntimeService


class DispatchExit(Exception):
    """Terminate a dispatch with a process-style exit status."""

    def __init__(self, status: int) -> None:
        super().__init__(f"dispatch exited with status {status}")
        self.status = status


def _write_json(out: TextIO, payload: object) -> None:
    from universal_agent.core import write_json

    write_json(out, to_json_value(payload, fallback_to_string=True), indent=True)


def _write_text(out: TextIO, payload: str) -> None:
    out.write(payload)


def _parse_domain_identity(value: str) -> DomainIdentity:
    if "@" not in value:
        raise ValueError(f"domain package dependency must be name@version: {value}")
    name, version = value.split("@", 1)
    if not name.strip() or not version.strip():
        raise ValueError(f"domain package dependency must be name@version: {value}")
    return DomainIdentity(name, version)


async def _dispatch_eval(
    args: argparse.Namespace,
    service: RuntimeService,
    out: TextIO,
) -> None:
    command = cast(str, args.eval_command)
    if command == "list":
        profile = cast(str, args.profile)
        if not service.accepts_profile(profile):
            raise ValueError(f"unknown profile: {profile}")
        suite = _evaluation_suite(args)
        scenarios = suite.select(_evaluation_selector(args))
        _write_json(out, _evaluation_list_body(suite, scenarios))
        return
    if command == "run":
        profile = cast(str, args.profile)
        if not service.accepts_profile(profile):
            raise ValueError(f"unknown profile: {profile}")
        report_dir = cast(str | None, args.report_dir)
        suite_config = _evaluation_suite_config(args)
        result = await EvaluationRunner(
            service,
            report_store=None if report_dir is None else FileEvaluationReportStore(report_dir),
        ).run_suite(
            suite_config.suite,
            selector=_evaluation_selector(args),
            gate=_evaluation_quality_gate(args, suite_config.quality_gate),
        )
        if cast(str, args.format) == "junit":
            _write_text(out, encode_evaluation_junit_xml(result.recording))
            _write_text(out, "\n")
        else:
            _write_json(out, _evaluation_run_body(result, report_dir))
        if cast(bool, args.fail_on_fail) and not result.passed:
            raise DispatchExit(1)
        return
    if command == "replay":
        payload = await _dispatch_eval_replay(args, service)
        _write_json(out, payload)
        if cast(bool, args.fail_on_fail) and not cast(bool, payload["passed"]):
            raise DispatchExit(1)
        return
    if command == "recordings":
        recording_dir = cast(str, args.recording_dir)
        recordings = FileReplayRecordingStore(recording_dir).list_recordings()
        _write_json(out, _replay_recordings_body(recording_dir, recordings))
        return
    if command == "compare":
        comparison = compare_evaluation_reports(
            _load_evaluation_report(Path(cast(str, args.expected))),
            _load_evaluation_report(Path(cast(str, args.actual))),
        )
        _write_json(out, _evaluation_comparison_body(comparison))
        if cast(bool, args.fail_on_fail) and not comparison.passed:
            raise DispatchExit(1)
        return
    if command == "reports":
        report_dir = cast(str, args.report_dir)
        reports = FileEvaluationReportStore(report_dir).list_reports()
        _write_json(out, _evaluation_reports_body(report_dir, reports))
        return
    if command == "console":
        report_dir = cast(str, args.report_dir)
        snapshot = build_evaluation_console_snapshot(report_dir)
        if cast(str, args.format) == "text":
            _write_text(out, render_evaluation_console_text(snapshot))
            return
        _write_text(out, render_evaluation_console(snapshot))
        return
    if command == "datasets":
        registry = _evaluation_dataset_registry(args)
        if cast(bool, args.verify):
            _write_json(out, evaluation_dataset_verification_body(registry.verify()))
            return
        domain = cast(str | None, args.domain)
        _write_json(
            out,
            {
                "datasets": [
                    _evaluation_dataset_body(dataset)
                    for dataset in registry.list(
                        tag=cast(str | None, args.tag),
                        domain=None if domain is None else _parse_domain_identity(domain),
                    )
                ]
            },
        )
        return
    if command == "dataset":
        registry = _evaluation_dataset_registry(args)
        version = cast(str | None, args.version)
        dataset = (
            registry.get_by_name(cast(str, args.name))
            if version is None
            else registry.get(EvaluationDatasetIdentity(cast(str, args.name), version))
        )
        _write_json(out, _evaluation_dataset_body(dataset))
        return
    raise ValueError(f"unknown eval command: {command}")


def _evaluation_dataset_registry(args: argparse.Namespace) -> EvaluationDatasetRegistry:
    registry = EvaluationDatasetRegistry()
    registry.discover(Path(cast(str, args.dataset_dir)))
    return registry


def _evaluation_selector(args: argparse.Namespace) -> EvaluationScenarioSelector | None:
    kinds = cast(list[str] | None, args.kind)
    tags = cast(list[str] | None, args.tag)
    exclude_tags = cast(list[str] | None, args.exclude_tag)
    if kinds is None and tags is None and exclude_tags is None:
        return None
    return EvaluationScenarioSelector(
        kinds=None if kinds is None else tuple(EvaluationScenarioKind(item) for item in kinds),
        tags=tuple(tags or ()),
        exclude_tags=tuple(exclude_tags or ()),
    )


async def _dispatch_eval_replay(
    args: argparse.Namespace,
    service: RuntimeService,
) -> dict[str, object]:
    profile = cast(str, args.profile)
    if not service.accepts_profile(profile):
        raise ValueError(f"unknown profile: {profile}")
    suite = _evaluation_suite(args)
    scenarios = suite.select(_evaluation_selector(args))
    if not scenarios:
        raise ValueError("evaluation replay selected no scenarios")

    recording_dir = cast(str, args.recording_dir)
    store = FileReplayRecordingStore(recording_dir)
    harness = DeterministicReplayHarness(service)
    if cast(bool, args.update):
        recordings = []
        for scenario in scenarios:
            recording = await harness.record(scenario)
            store.save(recording)
            recordings.append(recording)
        return {
            "mode": "record",
            "passed": True,
            "suite_name": suite.name,
            "recording_dir": recording_dir,
            "scenario_count": len(recordings),
            "scenarios": [encode_replay_recording(item) for item in recordings],
        }

    reports = []
    for scenario in scenarios:
        try:
            expected = store.load(scenario.name)
        except ReplayRecordingNotFoundError as exc:
            raise ValueError(str(exc)) from exc
        reports.append(await harness.replay(scenario, expected))
    return {
        "mode": "replay",
        "passed": all(report.passed for report in reports),
        "suite_name": suite.name,
        "recording_dir": recording_dir,
        "scenario_count": len(reports),
        "scenarios": [_replay_report_body(report) for report in reports],
    }


def _evaluation_suite(args: argparse.Namespace) -> EvaluationSuite:
    return _evaluation_suite_config(args).suite


def _evaluation_suite_config(args: argparse.Namespace) -> EvaluationSuiteConfig:
    suite_file = cast(str | None, args.suite_file)
    if suite_file is not None:
        return load_evaluation_suite_config(suite_file)
    return EvaluationSuiteConfig(_local_evaluation_suite(cast(str, args.suite)))


def _evaluation_quality_gate(
    args: argparse.Namespace,
    suite_gate: EvaluationQualityGate | None,
) -> EvaluationQualityGate | None:
    overrides = {
        "min_pass_rate": cast(float | None, args.min_pass_rate),
        "min_goal_completion_rate": cast(float | None, args.min_goal_completion_rate),
        "min_task_success_rate": cast(float | None, args.min_task_success_rate),
        "min_action_success_rate": cast(float | None, args.min_action_success_rate),
        "max_tool_failure_rate": cast(float | None, args.max_tool_failure_rate),
        "max_policy_denial_rate": cast(float | None, args.max_policy_denial_rate),
        "max_average_recoveries_per_scenario": cast(
            float | None,
            args.max_average_recoveries,
        ),
        "max_human_intervention_rate": cast(float | None, args.max_human_intervention_rate),
        "max_average_actions_per_scenario": cast(float | None, args.max_average_actions),
        "max_average_active_resource_locks_per_scenario": cast(
            float | None,
            args.max_average_active_resource_locks,
        ),
        "max_average_execution_duration_ms_per_scenario": cast(
            float | None,
            args.max_average_duration_ms,
        ),
        "max_average_model_calls_per_scenario": cast(float | None, args.max_average_model_calls),
        "max_average_model_tokens_per_scenario": cast(
            float | None,
            args.max_average_model_tokens,
        ),
        "max_resource_conflict_rate": cast(float | None, args.max_resource_conflict_rate),
    }
    cost_override = cast(int | None, args.max_total_model_cost_micros)
    if (
        suite_gate is None
        and cost_override is None
        and all(value is None for value in overrides.values())
    ):
        return None
    base = EvaluationQualityGate() if suite_gate is None else suite_gate
    return EvaluationQualityGate(
        min_pass_rate=overrides["min_pass_rate"]
        if overrides["min_pass_rate"] is not None
        else base.min_pass_rate,
        min_goal_completion_rate=overrides["min_goal_completion_rate"]
        if overrides["min_goal_completion_rate"] is not None
        else base.min_goal_completion_rate,
        min_task_success_rate=overrides["min_task_success_rate"]
        if overrides["min_task_success_rate"] is not None
        else base.min_task_success_rate,
        min_action_success_rate=overrides["min_action_success_rate"]
        if overrides["min_action_success_rate"] is not None
        else base.min_action_success_rate,
        max_tool_failure_rate=overrides["max_tool_failure_rate"]
        if overrides["max_tool_failure_rate"] is not None
        else base.max_tool_failure_rate,
        max_policy_denial_rate=overrides["max_policy_denial_rate"]
        if overrides["max_policy_denial_rate"] is not None
        else base.max_policy_denial_rate,
        max_average_recoveries_per_scenario=overrides["max_average_recoveries_per_scenario"]
        if overrides["max_average_recoveries_per_scenario"] is not None
        else base.max_average_recoveries_per_scenario,
        max_human_intervention_rate=overrides["max_human_intervention_rate"]
        if overrides["max_human_intervention_rate"] is not None
        else base.max_human_intervention_rate,
        max_resource_conflict_rate=overrides["max_resource_conflict_rate"]
        if overrides["max_resource_conflict_rate"] is not None
        else base.max_resource_conflict_rate,
        max_average_active_resource_locks_per_scenario=overrides[
            "max_average_active_resource_locks_per_scenario"
        ]
        if overrides["max_average_active_resource_locks_per_scenario"] is not None
        else base.max_average_active_resource_locks_per_scenario,
        max_average_actions_per_scenario=overrides["max_average_actions_per_scenario"]
        if overrides["max_average_actions_per_scenario"] is not None
        else base.max_average_actions_per_scenario,
        max_average_execution_duration_ms_per_scenario=overrides[
            "max_average_execution_duration_ms_per_scenario"
        ]
        if overrides["max_average_execution_duration_ms_per_scenario"] is not None
        else base.max_average_execution_duration_ms_per_scenario,
        max_average_model_calls_per_scenario=overrides["max_average_model_calls_per_scenario"]
        if overrides["max_average_model_calls_per_scenario"] is not None
        else base.max_average_model_calls_per_scenario,
        max_average_model_tokens_per_scenario=overrides["max_average_model_tokens_per_scenario"]
        if overrides["max_average_model_tokens_per_scenario"] is not None
        else base.max_average_model_tokens_per_scenario,
        max_total_model_estimated_cost_micros=cost_override
        if cost_override is not None
        else base.max_total_model_estimated_cost_micros,
    )


def _local_evaluation_suite(name: str) -> EvaluationSuite:
    goal = Goal("Evaluate workload health", (SuccessCriterion("healthy", True),))
    task = Task("Inspect workload", ("healthy",))
    return EvaluationSuite(
        name,
        (
            EvaluationScenario(
                "healthy workload",
                goal,
                task,
                ScenarioExpectations(
                    expected_status=ExecutionStatus.COMPLETED,
                    expected_criteria=immutable_json({"healthy": True}),
                    required_events=("GoalCompleted", "EvaluationCompleted"),
                    required_evidence_claims=("healthy",),
                    required_capabilities=("inspect_workload",),
                    max_actions=1,
                ),
                kind=EvaluationScenarioKind.REGRESSION,
                tags=("smoke", "kubernetes"),
            ),
            EvaluationScenario(
                "invalid scale policy",
                goal,
                task,
                ScenarioExpectations(
                    expected_status=ExecutionStatus.FAILED,
                    expected_error_code=ErrorCode.POLICY_DENIED,
                    forbidden_events=("ActionStarted",),
                    required_audit_capabilities=("scale_workload",),
                    policy_denial_count=1,
                    max_actions=0,
                ),
                kind=EvaluationScenarioKind.POLICY,
                tags=("policy", "kubernetes"),
            ),
        ),
        tags=("local", "kubernetes"),
    )


def _load_evaluation_report(path: Path) -> EvaluationReportRecording:
    return decode_evaluation_report(json_mapping(read_json_file(path)))


def _object_body(value: object) -> dict[str, object]:
    return cast(dict[str, object], to_json_object(value, fallback_to_string=True))


def _evaluation_run_body(
    result: EvaluationRunResult,
    report_dir: str | None,
) -> dict[str, object]:
    return {
        "passed": result.passed,
        "suite": _evaluation_report_body(result.recording),
        "gate": (
            None if result.recording.gate is None else _evaluation_gate_body(result.recording.gate)
        ),
        "report_dir": report_dir,
    }


def _evaluation_reports_body(
    report_dir: str,
    reports: tuple[EvaluationReportRecording, ...],
) -> dict[str, object]:
    return {
        "report_dir": report_dir,
        "report_count": len(reports),
        "reports": [_evaluation_report_summary_body(item) for item in reports],
    }


def _evaluation_report_summary_body(recording: EvaluationReportRecording) -> dict[str, object]:
    return {
        "suite_name": recording.suite_name,
        "passed": recording.passed,
        "scenario_count": recording.summary.scenario_count,
        "passed_count": recording.summary.passed_count,
        "failed_count": recording.summary.failed_count,
        "gate_passed": None if recording.gate is None else recording.gate.passed,
        "failed_scenarios": [
            scenario.scenario_name for scenario in recording.scenarios if not scenario.passed
        ],
        "execution_duration_ms": recording.summary.execution_duration_ms,
        "model_total_token_count": recording.summary.model_total_token_count,
        "model_estimated_cost_micros": recording.summary.model_estimated_cost_micros,
    }


def _evaluation_list_body(
    suite: EvaluationSuite,
    scenarios: tuple[EvaluationScenario, ...],
) -> dict[str, object]:
    return {
        "suite_name": suite.name,
        "suite_tags": list(suite.tags),
        "scenario_count": len(scenarios),
        "scenarios": [_evaluation_scenario_definition_body(item) for item in scenarios],
    }


def evaluation_dataset_verification_body(
    report: EvaluationDatasetVerificationReport,
) -> dict[str, object]:
    return {
        "passed": report.passed,
        "failed_check_count": len(report.failed_checks),
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "message": check.message,
            }
            for check in report.checks
        ],
    }


def _evaluation_dataset_body(dataset: EvaluationDataset) -> dict[str, object]:
    return {
        "name": dataset.identity.name,
        "version": dataset.identity.version,
        "description": dataset.manifest.description,
        "author": dataset.manifest.author,
        "tags": list(dataset.manifest.tags),
        "domains": [
            {"name": domain.name, "version": domain.version} for domain in dataset.manifest.domains
        ],
        "suite_count": len(dataset.manifest.suites),
        "suites": [
            {
                "name": suite.name,
                "path": suite.path,
                "description": suite.description,
                "tags": list(suite.tags),
                "suite_path": str(dataset.suite_path(suite)),
            }
            for suite in dataset.manifest.suites
        ],
        "root_path": str(dataset.root_path),
        "manifest_path": str(dataset.manifest_path),
    }


def _evaluation_scenario_definition_body(scenario: EvaluationScenario) -> dict[str, object]:
    return {
        "scenario_name": scenario.name,
        "kind": scenario.kind.value,
        "tags": list(scenario.tags),
        "goal": {
            "description": scenario.goal.description,
            "success_criteria": [item.key for item in scenario.goal.success_criteria],
        },
        "task": {
            "description": scenario.task.description,
            "required_criteria": list(scenario.task.required_criteria),
        },
    }


def _evaluation_report_body(recording: EvaluationReportRecording) -> dict[str, object]:
    body = _object_body(recording)
    body["summary"] = _evaluation_summary_body(recording.summary)
    body["scenarios"] = [_evaluation_scenario_body(item) for item in recording.scenarios]
    body.pop("gate", None)
    return body


def _evaluation_summary_body(summary: EvaluationSummaryRecording) -> dict[str, object]:
    return _object_body(summary)


def _evaluation_scenario_body(scenario: EvaluationScenarioRecording) -> dict[str, object]:
    body = _object_body(scenario)
    body["checks"] = [_evaluation_check_body(check) for check in scenario.checks]
    body.pop("metrics", None)
    return body


def _evaluation_gate_body(gate: EvaluationGateRecording) -> dict[str, object]:
    return _object_body(gate)


def _evaluation_check_body(check: EvaluationCheckRecording) -> dict[str, object]:
    return _object_body(check)


def _replay_report_body(report: ReplayReport) -> dict[str, object]:
    return {
        "scenario_name": report.actual.scenario_name,
        "passed": report.passed,
        "checks": [_replay_check_body(check) for check in report.checks],
        "failed_checks": [_replay_check_body(check) for check in report.failed_checks],
        "expected": encode_replay_recording(report.expected),
        "actual": encode_replay_recording(report.actual),
    }


def _replay_recordings_body(
    recording_dir: str,
    recordings: tuple[ReplayRecording, ...],
) -> dict[str, object]:
    return {
        "recording_dir": recording_dir,
        "recording_count": len(recordings),
        "recordings": [_replay_recording_summary_body(item) for item in recordings],
    }


def _replay_recording_summary_body(recording: ReplayRecording) -> dict[str, object]:
    return {
        "scenario_name": recording.scenario_name,
        "result_status": recording.result_status.value,
        "error_code": None if recording.error_code is None else recording.error_code.value,
        "event_count": recording.metrics.event_count,
        "action_started_count": recording.metrics.action_started_count,
        "policy_denial_count": recording.metrics.policy_denial_count,
        "recovery_planned_count": recording.metrics.recovery_planned_count,
        "resource_conflict_count": recording.metrics.resource_conflict_count,
        "model_total_token_count": recording.metrics.model_total_token_count,
        "model_estimated_cost_micros": recording.metrics.model_estimated_cost_micros,
        "action_capabilities": list(recording.action_capabilities),
        "policy_effects": list(recording.policy_effects),
        "audit_capabilities": [item.capability for item in recording.audit_entries],
    }


def _replay_check_body(check: ReplayCheck) -> dict[str, object]:
    return _object_body(check)


def _evaluation_comparison_body(comparison: EvaluationReportComparison) -> dict[str, object]:
    return {
        "passed": comparison.passed,
        "checks": [_comparison_check_body(check) for check in comparison.checks],
        "failed_checks": [_comparison_check_body(check) for check in comparison.failed_checks],
    }


def _comparison_check_body(check: EvaluationReportComparisonCheck) -> dict[str, object]:
    return _object_body(check)
