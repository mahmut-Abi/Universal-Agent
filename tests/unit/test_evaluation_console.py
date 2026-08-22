from __future__ import annotations

from pathlib import Path

from universal_agent.core import ExecutionStatus, immutable_json
from universal_agent.evaluation.console import (
    EvaluationConsoleSnapshot,
    build_evaluation_console_snapshot,
    render_evaluation_console,
)
from universal_agent.evaluation.harness import EvaluationScenarioKind
from universal_agent.evaluation.recording import (
    EvaluationCheckRecording,
    EvaluationGateRecording,
    EvaluationReportRecording,
    EvaluationScenarioRecording,
    EvaluationSummaryRecording,
    FileEvaluationReportStore,
)


def test_evaluation_console_renders_and_escapes_report_snapshot() -> None:
    report = evaluation_report("daily <suite>")
    snapshot = EvaluationConsoleSnapshot("/tmp/reports", (report,))

    rendered = render_evaluation_console(snapshot)

    assert "Evaluation Console" in rendered
    assert "daily &lt;suite&gt;" in rendered
    assert "daily <suite>" not in rendered
    assert "healthy &lt;workload&gt;" in rendered
    assert "Scenario Results" in rendered
    assert "inspect_workload" in rendered
    assert "healthy" in rendered
    assert "Quality Gate Checks" in rendered
    assert "min_pass_rate" in rendered
    assert "Gate Failures" in rendered


def test_evaluation_console_handles_empty_report_directory(tmp_path: Path) -> None:
    snapshot = build_evaluation_console_snapshot(tmp_path / "missing")

    rendered = render_evaluation_console(snapshot)

    assert snapshot.reports == ()
    assert "No evaluation reports" in rendered
    assert "No scenarios" in rendered
    assert "No gate checks" in rendered


def test_evaluation_console_snapshot_loads_file_report_store(tmp_path: Path) -> None:
    report = evaluation_report("daily regression suite")
    FileEvaluationReportStore(tmp_path).save(report)

    snapshot = build_evaluation_console_snapshot(tmp_path)

    assert snapshot.report_dir == str(tmp_path)
    assert tuple(item.suite_name for item in snapshot.reports) == ("daily regression suite",)


def evaluation_report(suite_name: str) -> EvaluationReportRecording:
    return EvaluationReportRecording(
        suite_name,
        False,
        EvaluationSummaryRecording(
            scenario_count=1,
            passed_count=0,
            failed_count=1,
            goal_completed_count=0,
            task_completed_count=0,
            action_started_count=1,
            action_completed_count=1,
            tool_failure_count=0,
            policy_denial_count=0,
            recovery_planned_count=0,
            human_intervention_count=0,
            execution_duration_ms=42,
            model_total_token_count=123,
            model_estimated_cost_micros=7,
        ),
        (
            EvaluationScenarioRecording(
                "healthy <workload>",
                False,
                ExecutionStatus.COMPLETED,
                None,
                kind=EvaluationScenarioKind.REGRESSION,
                tags=("smoke", "kubernetes"),
                satisfied_criteria=immutable_json({"healthy": True}),
                checks=(
                    EvaluationCheckRecording("status", True, "matched"),
                    EvaluationCheckRecording("evidence", False, "missing claim"),
                ),
                event_types=("ActionStarted", "GoalCompleted"),
                action_capabilities=("inspect_workload",),
                audit_capabilities=(),
                evidence_claims=("healthy",),
            ),
        ),
        EvaluationGateRecording(
            False,
            (EvaluationCheckRecording("min_pass_rate", False, "expected pass rate >= 1.0"),),
        ),
    )
