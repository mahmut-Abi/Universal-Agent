from __future__ import annotations

from dataclasses import dataclass
from html import escape
from io import StringIO
from pathlib import Path

from rich.console import Console
from rich.text import Text

from universal_agent.evaluation.recording import (
    EvaluationCheckRecording,
    EvaluationGateRecording,
    EvaluationReportRecording,
    EvaluationScenarioRecording,
    FileEvaluationReportStore,
)
from universal_agent.web_ui import _page


@dataclass(frozen=True, slots=True)
class EvaluationConsoleSnapshot:
    """Read-only projection of persisted evaluation reports for application UIs."""

    report_dir: str
    reports: tuple[EvaluationReportRecording, ...]


_TEXT_SECTION_TITLES = frozenset(
    {
        "Universal Agent Evaluation Console",
        "Evaluation Reports",
        "Scenario Results",
        "Quality Gate Checks",
    }
)


def build_evaluation_console_snapshot(report_dir: str | Path) -> EvaluationConsoleSnapshot:
    """Load persisted evaluation reports without depending on Runtime internals."""

    path = Path(report_dir)
    return EvaluationConsoleSnapshot(str(path), FileEvaluationReportStore(path).list_reports())


def render_evaluation_console(snapshot: EvaluationConsoleSnapshot) -> str:
    title = "Universal Agent Evaluation Console"
    return _page(
        title,
        (
            _hero(snapshot),
            '<section class="grid cards" aria-label="Evaluation summary">',
            _metric_card("Suites", len(snapshot.reports)),
            _metric_card("Scenarios", _scenario_count(snapshot.reports)),
            _metric_card("Passed", _passed_count(snapshot.reports)),
            _metric_card("Failed", _failed_count(snapshot.reports)),
            _metric_card("Gate Failures", _gate_failure_count(snapshot.reports)),
            _metric_card("Tokens", _token_count(snapshot.reports)),
            "</section>",
            _reports(snapshot.reports),
            _scenarios(snapshot.reports),
            _gate_checks(snapshot.reports),
        ),
        stylesheet=_stylesheet(),
    )


def render_evaluation_console_text(snapshot: EvaluationConsoleSnapshot) -> str:
    """Render persisted evaluation reports as deterministic terminal text."""

    lines = [
        "Universal Agent Evaluation Console",
        _rule(),
        f"Report Dir: {snapshot.report_dir}",
        (
            "Summary: "
            f"suites={len(snapshot.reports)} "
            f"scenarios={_scenario_count(snapshot.reports)} "
            f"passed={_passed_count(snapshot.reports)} "
            f"failed={_failed_count(snapshot.reports)} "
            f"gate_failures={_gate_failure_count(snapshot.reports)} "
            f"tokens={_token_count(snapshot.reports)}"
        ),
        "",
        "Evaluation Reports",
        _rule(),
    ]
    lines.extend(_report_text_lines(snapshot.reports))
    lines.extend(("", "Scenario Results", _rule()))
    lines.extend(_scenario_text_lines(snapshot.reports))
    lines.extend(("", "Quality Gate Checks", _rule()))
    lines.extend(_gate_check_text_lines(snapshot.reports))
    return _render_text_lines(lines)


def _render_text_lines(lines: list[str]) -> str:
    buffer = StringIO()
    console = Console(
        file=buffer,
        force_terminal=False,
        color_system=None,
        highlight=False,
        width=240,
    )
    for line in lines:
        console.print(_rich_text_line(line), markup=False, highlight=False, soft_wrap=True)
    return buffer.getvalue()


def _rich_text_line(line: str) -> Text:
    if line in _TEXT_SECTION_TITLES:
        return Text(line, style="bold")
    if " status=fail" in line or " gate=fail" in line:
        return Text(line, style="red")
    if " status=pass" in line or " gate=pass" in line:
        return Text(line, style="green")
    return Text(line)


def _report_text_lines(reports: tuple[EvaluationReportRecording, ...]) -> list[str]:
    if not reports:
        return ["- none"]
    return [
        (
            f"- {report.suite_name}"
            f" status={_pass_text(report.passed)}"
            f" scenarios={report.summary.scenario_count}"
            f" passed={report.summary.passed_count}"
            f" failed={report.summary.failed_count}"
            f" gate={_gate_text(report.gate)}"
            f" failed_scenarios={_failed_scenario_text(report)}"
            f" duration_ms={report.summary.execution_duration_ms}"
            f" tokens={report.summary.model_total_token_count}"
            f" cost_micros={report.summary.model_estimated_cost_micros}"
        )
        for report in reports
    ]


def _scenario_text_lines(reports: tuple[EvaluationReportRecording, ...]) -> list[str]:
    rows = [
        (
            f"- {report.suite_name}/{scenario.scenario_name}"
            f" kind={scenario.kind.value}"
            f" tags={_tuple_text(scenario.tags)}"
            f" status={_pass_text(scenario.passed)}"
            f" result={_result_text(scenario)}"
            f" checks={_checks_text(scenario.checks)}"
            f" capabilities={_tuple_text(scenario.action_capabilities)}"
            f" evidence={_tuple_text(scenario.evidence_claims)}"
        )
        for report in reports
        for scenario in report.scenarios
    ]
    return rows or ["- none"]


def _gate_check_text_lines(reports: tuple[EvaluationReportRecording, ...]) -> list[str]:
    rows = [
        (
            f"- {report.suite_name}/{check.name}"
            f" status={_pass_text(check.passed)}"
            f" message={check.message}"
        )
        for report in reports
        if report.gate is not None
        for check in report.gate.checks
    ]
    return rows or ["- none"]


def _hero(snapshot: EvaluationConsoleSnapshot) -> str:
    return "\n".join(
        (
            '<section class="hero">',
            "<div>",
            "<p>Universal Agent Runtime</p>",
            "<h1>Evaluation Console</h1>",
            f"<span>report_dir={_html(snapshot.report_dir)}</span>",
            "</div>",
            '<div class="status">',
            f'<span class="pill ok">Reports: {len(snapshot.reports)}</span>',
            "</div>",
            "</section>",
        )
    )


def _reports(reports: tuple[EvaluationReportRecording, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(report.suite_name)}</td>",
                f"<td>{_status_pill(report.passed)}</td>",
                f"<td>{report.summary.scenario_count}</td>",
                f"<td>{report.summary.passed_count}</td>",
                f"<td>{report.summary.failed_count}</td>",
                f"<td>{_html(_gate_text(report.gate))}</td>",
                f"<td>{_html(_failed_scenario_text(report))}</td>",
                f"<td>{report.summary.execution_duration_ms}</td>",
                f"<td>{report.summary.model_total_token_count}</td>",
                f"<td>{report.summary.model_estimated_cost_micros}</td>",
                "</tr>",
            )
        )
        for report in reports
    ]
    if not rows:
        rows.append('<tr><td colspan="10">No evaluation reports</td></tr>')
    return _section(
        "Evaluation Reports",
        _table(
            (
                "Suite",
                "Status",
                "Scenarios",
                "Passed",
                "Failed",
                "Gate",
                "Failed Scenarios",
                "Duration ms",
                "Tokens",
                "Cost micros",
            ),
            tuple(rows),
        ),
    )


def _scenarios(reports: tuple[EvaluationReportRecording, ...]) -> str:
    rows = [_scenario_row(report, scenario) for report in reports for scenario in report.scenarios]
    if not rows:
        rows.append('<tr><td colspan="9">No scenarios</td></tr>')
    return _section(
        "Scenario Results",
        _table(
            (
                "Suite",
                "Scenario",
                "Kind",
                "Tags",
                "Status",
                "Result",
                "Checks",
                "Capabilities",
                "Evidence",
            ),
            tuple(rows),
        ),
    )


def _scenario_row(
    report: EvaluationReportRecording,
    scenario: EvaluationScenarioRecording,
) -> str:
    return "\n".join(
        (
            "<tr>",
            f"<td>{_html(report.suite_name)}</td>",
            f"<td>{_html(scenario.scenario_name)}</td>",
            f"<td>{_html(scenario.kind.value)}</td>",
            f"<td>{_html(_tuple_text(scenario.tags))}</td>",
            f"<td>{_status_pill(scenario.passed)}</td>",
            f"<td>{_html(_result_text(scenario))}</td>",
            f"<td>{_html(_checks_text(scenario.checks))}</td>",
            f"<td>{_html(_tuple_text(scenario.action_capabilities))}</td>",
            f"<td>{_html(_tuple_text(scenario.evidence_claims))}</td>",
            "</tr>",
        )
    )


def _gate_checks(reports: tuple[EvaluationReportRecording, ...]) -> str:
    rows = [
        "\n".join(
            (
                "<tr>",
                f"<td>{_html(report.suite_name)}</td>",
                f"<td>{_html(check.name)}</td>",
                f"<td>{_status_pill(check.passed)}</td>",
                f"<td>{_html(check.message)}</td>",
                "</tr>",
            )
        )
        for report in reports
        if report.gate is not None
        for check in report.gate.checks
    ]
    if not rows:
        rows.append('<tr><td colspan="4">No gate checks</td></tr>')
    return _section(
        "Quality Gate Checks",
        _table(("Suite", "Check", "Status", "Message"), tuple(rows)),
    )


def _section(title: str, body: str) -> str:
    return "\n".join(
        (
            '<section class="panel">',
            f"<h2>{_html(title)}</h2>",
            body,
            "</section>",
        )
    )


def _metric_card(label: str, value: object) -> str:
    return "\n".join(
        (
            '<article class="card">',
            f"<span>{_html(label)}</span>",
            f"<strong>{_html(value)}</strong>",
            "</article>",
        )
    )


def _table(headers: tuple[str, ...], rows: tuple[str, ...]) -> str:
    header = "".join(f"<th>{_html(item)}</th>" for item in headers)
    return (
        '<div class="table-wrap"><table><thead><tr>'
        + header
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _scenario_count(reports: tuple[EvaluationReportRecording, ...]) -> int:
    return sum(report.summary.scenario_count for report in reports)


def _passed_count(reports: tuple[EvaluationReportRecording, ...]) -> int:
    return sum(report.summary.passed_count for report in reports)


def _failed_count(reports: tuple[EvaluationReportRecording, ...]) -> int:
    return sum(report.summary.failed_count for report in reports)


def _gate_failure_count(reports: tuple[EvaluationReportRecording, ...]) -> int:
    return sum(
        1
        for report in reports
        if report.gate is not None
        for check in report.gate.checks
        if not check.passed
    )


def _token_count(reports: tuple[EvaluationReportRecording, ...]) -> int:
    return sum(report.summary.model_total_token_count for report in reports)


def _gate_text(gate: EvaluationGateRecording | None) -> str:
    if gate is None:
        return "none"
    failed = sum(1 for check in gate.checks if not check.passed)
    return f"{_pass_text(gate.passed)} ({failed} failed)"


def _failed_scenario_text(report: EvaluationReportRecording) -> str:
    failed = tuple(scenario.scenario_name for scenario in report.scenarios if not scenario.passed)
    return _tuple_text(failed)


def _result_text(scenario: EvaluationScenarioRecording) -> str:
    if scenario.error_code is None:
        return scenario.result_status.value
    return f"{scenario.result_status.value}:{scenario.error_code.value}"


def _checks_text(checks: tuple[EvaluationCheckRecording, ...]) -> str:
    if not checks:
        return "none"
    failed = tuple(check.name for check in checks if not check.passed)
    if failed:
        return "failed: " + ", ".join(failed)
    return f"{len(checks)} passed"


def _tuple_text(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "none"


def _pass_text(passed: bool) -> str:
    return "pass" if passed else "fail"


def _rule() -> str:
    return "-" * 72


def _status_class(passed: bool) -> str:
    return "ok" if passed else "warn"


def _status_pill(passed: bool) -> str:
    return f'<span class="pill {_status_class(passed)}">{_pass_text(passed)}</span>'


def _html(value: object) -> str:
    return escape(str(value), quote=False)


def _stylesheet() -> str:
    return """
:root {
  color-scheme: light;
  font-family:
    Inter,
    ui-sans-serif,
    system-ui,
    -apple-system,
    BlinkMacSystemFont,
    "Segoe UI",
    sans-serif;
  background: #f6f7f9;
  color: #172033;
}
* {
  box-sizing: border-box;
}
body {
  margin: 0;
}
.shell {
  width: min(1180px, calc(100vw - 40px));
  margin: 0 auto;
  padding: 28px 0 40px;
}
.hero, .panel, .card {
  background: #ffffff;
  border: 1px solid #d9e1ec;
  border-radius: 8px;
  box-shadow: 0 1px 2px rgba(23, 32, 51, 0.05);
}
.hero {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  align-items: flex-start;
  padding: 24px;
  border-top: 4px solid #2563eb;
}
.hero p, .hero h1 {
  margin: 0;
}
.hero p {
  color: #5d6b82;
  font-size: 13px;
  font-weight: 700;
  text-transform: uppercase;
}
.hero h1 {
  margin-top: 4px;
  font-size: 30px;
  line-height: 1.1;
}
.hero span {
  display: inline-block;
  margin-top: 10px;
  color: #5d6b82;
  font-size: 14px;
}
.status {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
.pill {
  border-radius: 999px;
  padding: 6px 9px;
  font-size: 13px;
  font-weight: 700;
  white-space: nowrap;
}
.ok {
  background: #dcfce7;
  color: #166534;
}
.warn {
  background: #fee2e2;
  color: #991b1b;
}
.grid {
  display: grid;
  gap: 12px;
}
.cards {
  grid-template-columns: repeat(6, minmax(0, 1fr));
  margin: 16px 0;
}
.card {
  min-height: 82px;
  padding: 16px;
}
.card span {
  display: block;
  color: #5d6b82;
  font-size: 13px;
}
.card strong {
  display: block;
  margin-top: 8px;
  font-size: 24px;
}
.panel {
  margin-top: 16px;
  padding: 18px;
}
h2 {
  margin: 0 0 12px;
  font-size: 18px;
}
.table-wrap {
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
th, td {
  padding: 9px 10px;
  border-bottom: 1px solid #e5eaf1;
  text-align: left;
  vertical-align: top;
}
th {
  color: #46556b;
  font-size: 12px;
  text-transform: uppercase;
}
@media (max-width: 860px) {
  .hero {
    flex-direction: column;
  }
  .status {
    justify-content: flex-start;
  }
  .cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
"""


__all__ = [
    "EvaluationConsoleSnapshot",
    "build_evaluation_console_snapshot",
    "render_evaluation_console",
    "render_evaluation_console_text",
]
