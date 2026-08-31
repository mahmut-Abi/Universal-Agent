"""Unit tests for the Textual runtime TUI dashboard (projections + pilot)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from types import MappingProxyType

import pytest
from textual.widgets import DataTable, Static

from universal_agent.core import (
    GoalId,
    GoalStatus,
    SessionId,
    TaskId,
    TaskStatus,
)
from universal_agent.operations import (
    DoctorReportView,
    RuntimeCostView,
    RuntimeMetricsView,
)
from universal_agent.runtime import (
    RuntimeEventView,
    SessionSummaryView,
    SessionView,
)
from universal_agent.service import (
    HealthView,
    ReadyView,
    RuntimeConfigDomainView,
    RuntimeConfigView,
)
from universal_agent.terminal.tui import TuiSnapshot
from universal_agent.terminal.tui_app import (
    RuntimeTuiApp,
    session_detail_lines,
    session_table_rows,
)

pytestmark = pytest.mark.unit


def _session_summary(
    session_id: str,
    *,
    goal_status: GoalStatus = GoalStatus.COMPLETED,
    description: str = "Verify workload health",
) -> SessionSummaryView:
    return SessionSummaryView(
        SessionId(session_id),
        GoalId(f"goal-{session_id}"),
        description,
        goal_status,
        TaskId(f"task-{session_id}"),
        "Inspect workload",
        TaskStatus.COMPLETED,
        1,
        1,
        pending_action=False,
        termination_reason=None,
        error_code=None,
        domain_name="kubernetes",
        domain_version="0.2.0",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _selected_session(summary: SessionSummaryView) -> SessionView:
    return SessionView(
        summary.session_id,
        summary.goal_id,
        summary.goal_description,
        summary.goal_status,
        summary.current_task_id,
        summary.current_task_description,
        summary.current_task_status,
        summary.iteration,
        (),
        MappingProxyType({}),
        None,
        None,
        None,
        None,
        "kubernetes",
        "0.2.0",
    )


def _snapshot(
    *,
    sessions: tuple[SessionSummaryView, ...] = (),
    selected: SessionView | None = None,
    events: tuple[RuntimeEventView, ...] = (),
) -> TuiSnapshot:
    return TuiSnapshot(
        health=HealthView("ok", "universal-agent-runtime"),
        ready=ReadyView(True, "ready", 1, 1, 1),
        config=RuntimeConfigView(
            environment=MappingProxyType({}),
            domain_package_paths=(),
            store_backend="memory",
            store_path=None,
            distributed_queue_backend="memory",
            distributed_queue_path=None,
            distributed_locks_backend="memory",
            distributed_locks_path=None,
            distributed_workers_backend="memory",
            distributed_workers_path=None,
            max_iterations=20,
            max_recovery_steps=8,
            domains=(
                RuntimeConfigDomainView(
                    "kubernetes",
                    "0.2.0",
                    True,
                    "kubernetes_api",
                    MappingProxyType({}),
                ),
            ),
            secrets=(),
        ),
        domains=(),
        domain_packages=(),
        profiles=(),
        capabilities=(),
        tools=(),
        policies=(),
        evaluators=(),
        memories=(),
        metrics=RuntimeMetricsView(
            session_count=len(sessions),
            active_session_count=0,
            waiting_session_count=0,
            completed_goal_count=0,
            failed_goal_count=0,
            cancelled_goal_count=0,
            event_count=len(events),
            action_started_count=0,
            action_completed_count=0,
            tool_failure_count=0,
            policy_denial_count=0,
            confirmation_required_count=0,
            recovery_planned_count=0,
            recovery_exhausted_count=0,
            human_intervention_count=0,
            resource_lock_acquired_count=0,
            resource_lock_released_count=0,
            resource_conflict_count=0,
            active_resource_lock_count=0,
        ),
        cost=RuntimeCostView(0, 0, 0, 0, 0, "USD", ()),
        doctor=DoctorReportView("ok", ()),
        distributed_snapshot=None,
        distributed_health=None,
        sessions=sessions,
        selected_session=selected,
        session_explorer=None,
        events=events,
        audit_records=(),
    )


def _two_session_snapshot() -> TuiSnapshot:
    sessions = (
        _session_summary("s-1"),
        _session_summary("s-2", goal_status=GoalStatus.WAITING, description="Second goal"),
    )
    events = (
        RuntimeEventView(
            "ev-1",
            "ActionStarted",
            SessionId("s-1"),
            GoalId("goal-s-1"),
            TaskId("task-s-1"),
            None,
            MappingProxyType({}),
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    return _snapshot(sessions=sessions, selected=_selected_session(sessions[0]), events=events)


def fake_provider(
    snapshot: TuiSnapshot,
) -> tuple[
    Callable[[SessionId | None], Awaitable[TuiSnapshot]],
    list[SessionId | None],
]:
    calls: list[SessionId | None] = []

    async def provider(session_id: SessionId | None) -> TuiSnapshot:
        calls.append(session_id)
        return snapshot

    return provider, calls


def test_session_table_rows_projects_sessions() -> None:
    rows = session_table_rows(_two_session_snapshot())

    assert rows[0] == (
        "s-1",
        "completed",
        "Verify workload health — Inspect workload",
    )
    assert rows[1][0] == "s-2"
    assert rows[1][1] == "waiting"


def test_session_detail_lines_include_goal_events_and_counts() -> None:
    snapshot = _two_session_snapshot()
    summary = snapshot.sessions[1]

    lines = session_detail_lines(snapshot, 1, summary)

    assert "session: s-2" in lines
    assert "goal: waiting Second goal" in lines
    assert "Recent events" in lines
    assert any("ActionStarted" in line for line in lines)


@pytest.mark.asyncio
async def test_dashboard_lists_sessions_and_tracks_selection() -> None:
    snapshot = _two_session_snapshot()
    provider, calls = fake_provider(snapshot)
    app = RuntimeTuiApp(snapshot_provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        table = app.query_one("#sessions-table", DataTable)
        assert table.row_count == 2
        assert app._selected_index == 0

        await pilot.press("j")
        await pilot.pause()

        assert app._selected_index == 1
        assert table.cursor_row == 1
        assert calls[-1] == SessionId("s-2")

        detail = app.query_one("#detail", Static)
        content = session_detail_lines(snapshot, 1, snapshot.sessions[1])
        assert detail is not None
        assert "Second goal" in "".join(content)


@pytest.mark.asyncio
async def test_refresh_binding_reloads_snapshot() -> None:
    snapshot = _two_session_snapshot()
    provider, calls = fake_provider(snapshot)
    app = RuntimeTuiApp(snapshot_provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        assert len(calls) >= 1

        await pilot.press("r")
        await pilot.pause()
        await pilot.pause()

        assert len(calls) >= 2
        assert calls[-1] == SessionId("s-1")


@pytest.mark.asyncio
async def test_quit_binding_exits_cleanly() -> None:
    snapshot = _two_session_snapshot()
    provider, _ = fake_provider(snapshot)
    app = RuntimeTuiApp(snapshot_provider=provider)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")

    assert app.return_code == 0
