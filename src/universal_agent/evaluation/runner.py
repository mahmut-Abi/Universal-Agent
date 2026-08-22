from __future__ import annotations

from dataclasses import dataclass

from universal_agent.evaluation.harness import (
    EvaluationGateReport,
    EvaluationHarness,
    EvaluationQualityGate,
    EvaluationRuntime,
    EvaluationScenarioSelector,
    EvaluationSuite,
    EvaluationSuiteReport,
    evaluate_quality_gate,
)
from universal_agent.evaluation.recording import (
    EvaluationReportRecording,
    EvaluationReportStore,
    record_evaluation_suite,
)


@dataclass(frozen=True, slots=True)
class EvaluationRunResult:
    """Complete result of a suite run through the evaluation platform seam."""

    suite_report: EvaluationSuiteReport
    gate_report: EvaluationGateReport
    recording: EvaluationReportRecording

    @property
    def passed(self) -> bool:
        return self.suite_report.passed and self.gate_report.passed


class EvaluationRunner:
    """Run suites, apply quality gates and optionally persist stable reports."""

    def __init__(
        self,
        runtime: EvaluationRuntime,
        *,
        report_store: EvaluationReportStore | None = None,
    ) -> None:
        self._harness = EvaluationHarness(runtime)
        self._report_store = report_store

    async def run_suite(
        self,
        suite: EvaluationSuite,
        *,
        selector: EvaluationScenarioSelector | None = None,
        gate: EvaluationQualityGate | None = None,
        suite_name: str | None = None,
        save_recording: bool = True,
    ) -> EvaluationRunResult:
        suite_report = await self._harness.run_suite(suite, selector=selector)
        if suite_name is not None:
            suite_report = EvaluationSuiteReport(suite_report.reports, suite_name)
        gate_report = evaluate_quality_gate(suite_report, gate)
        recording = record_evaluation_suite(suite_report, gate_report=gate_report)
        if save_recording and self._report_store is not None:
            self._report_store.save(recording)
        return EvaluationRunResult(suite_report, gate_report, recording)
