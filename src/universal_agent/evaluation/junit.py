from __future__ import annotations

from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring


def encode_evaluation_junit_xml(recording: Any) -> str:
    """Encode a stable evaluation report as dependency-free JUnit XML."""

    gate_checks = () if recording.gate is None else recording.gate.checks
    failure_count = sum(1 for scenario in recording.scenarios if not scenario.passed) + sum(
        1 for check in gate_checks if not check.passed
    )
    root = Element(
        "testsuite",
        {
            "name": recording.suite_name,
            "tests": str(len(recording.scenarios) + len(gate_checks)),
            "failures": str(failure_count),
            "errors": "0",
            "skipped": "0",
            "time": _junit_seconds(recording.summary.execution_duration_ms),
        },
    )
    for scenario in recording.scenarios:
        _append_scenario_testcase(root, recording.suite_name, scenario)
    for check in gate_checks:
        _append_gate_testcase(root, recording.suite_name, check)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + tostring(
        root,
        encoding="unicode",
        short_empty_elements=True,
    )


def _append_scenario_testcase(
    root: Element,
    suite_name: str,
    scenario: Any,
) -> None:
    testcase = SubElement(
        root,
        "testcase",
        {
            "classname": suite_name,
            "name": scenario.scenario_name,
            "time": "0.000",
        },
    )
    if scenario.passed:
        return
    failed_checks = tuple(check for check in scenario.checks if not check.passed)
    message = _scenario_failure_message(scenario, failed_checks)
    failure = SubElement(
        testcase,
        "failure",
        {
            "message": message,
            "type": "evaluation_scenario_failure",
        },
    )
    failure.text = _scenario_failure_text(scenario, failed_checks)


def _append_gate_testcase(
    root: Element,
    suite_name: str,
    check: Any,
) -> None:
    testcase = SubElement(
        root,
        "testcase",
        {
            "classname": f"{suite_name}.quality_gate",
            "name": check.name,
            "time": "0.000",
        },
    )
    if check.passed:
        return
    failure = SubElement(
        testcase,
        "failure",
        {
            "message": check.message,
            "type": "evaluation_quality_gate_failure",
        },
    )
    failure.text = check.message


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
