"""Unit tests for the agentd web-console route handler.

Covers the handler-level contracts the integration suites cannot reach:
frontend-package degradation (fallback page when universal_agent_web is
absent), unknown-path pass-through to the main router, and the JSON
evaluations payload shape with a configured report directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from universal_agent.agentd import console_routes
from universal_agent.agentd.console_routes import handle_console_route
from universal_agent.core import ExecutionStatus
from universal_agent.evaluation.console import build_evaluation_console_snapshot
from universal_agent.evaluation.recording import (
    EvaluationReportRecording,
    EvaluationScenarioRecording,
    EvaluationSummaryRecording,
    FileEvaluationReportStore,
)


@dataclass
class StubRequest:
    body: dict[str, Any] = field(default_factory=dict)


class StubService:
    """Bare stand-in: console GET views and the evaluations payload never
    dispatch through RuntimeService, so no runtime behavior is faked here."""

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - safety net
        raise AssertionError(f"unexpected RuntimeService access: {name}")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_unknown_console_path_returns_none_for_main_router() -> None:
    response = await handle_console_route(
        StubService(),  # type: ignore[arg-type]
        None,
        StubRequest(),
        "GET",
        "/console/does-not-exist",
    )
    assert response is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_unknown_console_path_outside_console_prefix_returns_none() -> None:
    response = await handle_console_route(
        StubService(),  # type: ignore[arg-type]
        None,
        StubRequest(),
        "GET",
        "/v1/sessions",
    )
    assert response is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_console_asset_serves_javascript_and_css_content_types() -> None:
    js = await handle_console_route(
        StubService(),  # type: ignore[arg-type]
        None,
        StubRequest(),
        "GET",
        "/console/app.js",
    )
    assert js is not None
    assert js.status_code == 200
    assert "text/javascript" in js.headers["content-type"]

    css = await handle_console_route(
        StubService(),  # type: ignore[arg-type]
        None,
        StubRequest(),
        "GET",
        "/console/style.css",
    )
    assert css is not None
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_console_views_render_fallback_page_without_frontend_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_package(filename: str) -> bytes | None:
        return None

    monkeypatch.setattr(console_routes, "_asset_bytes", missing_package)

    for path in ("/console", "/console/sessions", "/console/world"):
        response = await handle_console_route(
            StubService(),  # type: ignore[arg-type]
            None,
            StubRequest(),
            "GET",
            path,
        )
        assert response is not None
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        text = response.text_body or ""
        assert "universal-agent-web" in text
        assert "/openapi.json" in text


@pytest.mark.asyncio
@pytest.mark.unit
async def test_console_evaluations_payload_lists_reports(tmp_path: Any) -> None:
    summary = EvaluationSummaryRecording(
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
    )
    report = EvaluationReportRecording(
        suite_name="remediation-suite",
        passed=False,
        summary=summary,
        scenarios=(
            EvaluationScenarioRecording(
                scenario_name="scale-down-recovers",
                passed=True,
                result_status=ExecutionStatus.COMPLETED,
                error_code=None,
            ),
            EvaluationScenarioRecording(
                scenario_name="policy-denial",
                passed=False,
                result_status=ExecutionStatus.FAILED,
                error_code=None,
            ),
        ),
    )
    report_dir = tmp_path / "reports"
    FileEvaluationReportStore(report_dir).save(report)

    payload = console_routes._evaluations_payload(
        StubService(),  # type: ignore[arg-type]
        str(report_dir),
    )
    assert payload["status"] == "ok"
    assert payload["report_dir"] == str(report_dir)
    suites = payload["reports"]
    assert len(suites) == 1
    assert suites[0]["suite_name"] == "remediation-suite"
    assert suites[0]["scenario_count"] == 2
    assert suites[0]["passed_count"] == 1
    assert suites[0]["failed_count"] == 1
    assert suites[0]["gate_passed"] is None
    assert suites[0]["failed_scenarios"] == ["policy-denial"]


@pytest.mark.unit
def test_evaluation_console_snapshot_ignores_non_report_files(
    tmp_path: Any,
) -> None:
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "notes.txt").write_text("not a report", encoding="utf-8")

    snapshot = build_evaluation_console_snapshot(str(report_dir))
    assert snapshot.reports == ()
