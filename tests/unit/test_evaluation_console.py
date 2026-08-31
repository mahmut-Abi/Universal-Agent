from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent.core import ExecutionStatus, immutable_json
from universal_agent.evaluation.console import (
    EvaluationConsoleSnapshot,
    build_evaluation_console_snapshot,
    render_evaluation_console,
    render_evaluation_console_text,
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


@pytest.mark.unit
def test_evaluation_console_renders_and_escapes_report_snapshot() -> None:
    report = evaluation_report("daily <suite>")
    snapshot = EvaluationConsoleSnapshot("/tmp/<reports>", (report,))

    rendered = render_evaluation_console(snapshot)

    assert "Evaluation Console" in rendered
    assert "report_dir=/tmp/&lt;reports&gt;" in rendered
    assert "report_dir=/tmp/<reports>" not in rendered
    assert "daily &lt;suite&gt;" in rendered
    assert "daily <suite>" not in rendered
    assert "healthy &lt;workload&gt;" in rendered
    assert "Scenario Results" in rendered
    assert "inspect_workload" in rendered
    assert "healthy" in rendered
    assert "Quality Gate Checks" in rendered
    assert "min_pass_rate" in rendered
    assert "Gate Failures" in rendered


@pytest.mark.unit
def test_evaluation_console_renders_terminal_text_snapshot() -> None:
    report = evaluation_report("daily regression suite")
    snapshot = EvaluationConsoleSnapshot("/tmp/reports", (report,))

    rendered = render_evaluation_console_text(snapshot)

    assert "\x1b[" not in rendered
    assert "Universal Agent Evaluation Console" in rendered
    assert "Report Dir: /tmp/reports" in rendered
    assert "Summary: suites=1 scenarios=1 passed=0 failed=1 gate_failures=1 tokens=123" in rendered
    assert "Evaluation Reports" in rendered
    assert "- daily regression suite status=fail scenarios=1 passed=0 failed=1" in rendered
    assert "Scenario Results" in rendered
    assert (
        "- daily regression suite/healthy <workload> kind=regression tags=smoke, kubernetes"
        in rendered
    )
    assert "checks=failed: evidence" in rendered
    assert "capabilities=inspect_workload evidence=healthy" in rendered
    assert "Quality Gate Checks" in rendered
    assert "- daily regression suite/min_pass_rate status=fail" in rendered


@pytest.mark.unit
def test_evaluation_console_handles_empty_report_directory(tmp_path: Path) -> None:
    snapshot = build_evaluation_console_snapshot(tmp_path / "missing")

    rendered = render_evaluation_console(snapshot)
    text_rendered = render_evaluation_console_text(snapshot)

    assert snapshot.reports == ()
    assert "No evaluation reports" in rendered
    assert "No scenarios" in rendered
    assert "No gate checks" in rendered
    expected_summary = "Summary: suites=0 scenarios=0 passed=0 failed=0 gate_failures=0 tokens=0"
    assert expected_summary in text_rendered
    assert text_rendered.count("- none") == 3


@pytest.mark.unit
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
