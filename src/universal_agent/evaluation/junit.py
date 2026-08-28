from __future__ import annotations

from typing import Any
from xml.etree.ElementTree import tostring

from junit_xml import TestCase, TestSuite  # type: ignore[import-untyped]


def encode_evaluation_junit_xml(recording: Any) -> str:
    """Encode a stable evaluation report as JUnit XML."""

    gate_checks = () if recording.gate is None else recording.gate.checks
    suite = TestSuite(
        recording.suite_name,
        [
            *(
                _scenario_testcase(recording.suite_name, scenario)
                for scenario in recording.scenarios
            ),
            *(_gate_testcase(recording.suite_name, check) for check in gate_checks),
        ],
    )
    root = suite.build_xml_doc(encoding="utf-8")
    root.set("time", _junit_seconds(recording.summary.execution_duration_ms))
    return '<?xml version="1.0" encoding="utf-8"?>\n' + tostring(
        root,
        encoding="unicode",
        short_empty_elements=True,
    )


def _scenario_testcase(
    suite_name: str,
    scenario: Any,
) -> TestCase:
    testcase = TestCase(scenario.scenario_name, classname=suite_name)
    if scenario.passed:
        return testcase
    failed_checks = tuple(check for check in scenario.checks if not check.passed)
    testcase.add_failure_info(
        message=_scenario_failure_message(scenario, failed_checks),
        output=_scenario_failure_text(scenario, failed_checks),
        failure_type="evaluation_scenario_failure",
    )
    return testcase


def _gate_testcase(
    suite_name: str,
    check: Any,
) -> TestCase:
    testcase = TestCase(check.name, classname=f"{suite_name}.quality_gate")
    if check.passed:
        return testcase
    testcase.add_failure_info(
        message=check.message,
        output=check.message,
        failure_type="evaluation_quality_gate_failure",
    )
    return testcase


def _scenario_failure_message(
    scenario: Any,
    failed_checks: tuple[Any, ...],
) -> str:
    if failed_checks:
        return "; ".join(f"{check.name}: {check.message}" for check in failed_checks)
    if scenario.error_code is not None:
        return f"result={scenario.result_status.value}, error_code={scenario.error_code.value}"
    return f"result={scenario.result_status.value}"


def _scenario_failure_text(
    scenario: Any,
    failed_checks: tuple[Any, ...],
) -> str:
    lines = [
        f"scenario={scenario.scenario_name}",
        f"kind={scenario.kind.value}",
        "tags=" + ",".join(scenario.tags),
        f"result_status={scenario.result_status.value}",
        "error_code=" + ("" if scenario.error_code is None else scenario.error_code.value),
    ]
    for check in failed_checks:
        lines.append(f"{check.name}: {check.message}")
    return "\n".join(lines)


def _junit_seconds(milliseconds: int) -> str:
    return f"{max(milliseconds, 0) / 1000:.3f}"
