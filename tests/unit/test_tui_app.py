"""Unit tests for the interactive TUI dashboard (state machine + layout)."""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from types import MappingProxyType

import pytest
from rich.console import Console

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
    TuiState,
    build_dashboard,
    handle_key,
    map_key,
    run_tui_app,
    selected_session_id,
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
    sessions: tuple[SessionSummaryView, ...] | None = None,
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
            session_count=len(sessions or ()),
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
        sessions=sessions or (),
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
    selected = _selected_session(sessions[0])
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
    return _snapshot(sessions=sessions, selected=selected, events=events)


def _dashboard_text(snapshot: TuiSnapshot, state: TuiState) -> str:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, width=120)
    console.print(build_dashboard(snapshot, state))
    return buffer.getvalue()


def test_map_key_translates_sequences_and_single_chars() -> None:
    assert map_key("\x1b[A") == "up"
    assert map_key("\x1b[B") == "down"
    assert map_key("\x1b[H") == "home"
    assert map_key("\x1b[F") == "end"
    assert map_key("j") == "down"
    assert map_key("k") == "up"
    assert map_key("q") == "quit"
    assert map_key("x") is None


def test_handle_key_moves_selection_with_wraparound() -> None:
    state = TuiState()
    down = handle_key(state, "down", 2)
    assert down.selected_index == 1
    wrapped = handle_key(down, "down", 2)
    assert wrapped.selected_index == 0
    up = handle_key(state, "up", 2)
    assert up.selected_index == 1
    first = handle_key(state, "home", 2)
    assert first.selected_index == 0
    last = handle_key(state, "end", 2)
    assert last.selected_index == 1


def test_handle_key_quit_refresh_and_unknown_keys() -> None:
    state = TuiState()
    assert handle_key(state, "quit", 2).quit_requested is True
    refreshed = handle_key(state, "refresh", 2)
    assert refreshed.refresh_requested is True
    assert refreshed.status_hint == "refreshing…"
    unknown = handle_key(state, "z", 2)
    assert unknown.status_hint == "unmapped key: 'z'"
    assert handle_key(state, None, 2) is state


def test_handle_key_without_sessions_reports_hint() -> None:
    state = handle_key(TuiState(), "down", 0)
    assert state.selected_index == 0
    assert state.status_hint == "no sessions to select"


def test_selected_session_id_maps_current_index() -> None:
    snapshot = _two_session_snapshot()
    first = selected_session_id(snapshot, TuiState())
    assert first is not None
    assert first == SessionId("s-1")
    moved = handle_key(TuiState(), "down", 2)
    second = selected_session_id(snapshot, moved)
    assert second is not None
    assert second == SessionId("s-2")
    beyond = TuiState(selected_index=9)
    assert selected_session_id(snapshot, beyond) is None


def test_build_dashboard_renders_selection_detail_and_hints() -> None:
    snapshot = _two_session_snapshot()
    state = handle_key(TuiState(), "down", 2)
    text = _dashboard_text(snapshot, state)

    assert "Universal Agent Runtime TUI" in text
    assert "health=ok" in text
    assert "> " in text
    assert "s-2" in text
    assert "Second goal" in text
    assert "goal: waiting Second goal" in text
    assert "ActionStarted" in text
    assert "q: quit" in text


def test_build_dashboard_without_sessions_renders_empty_hint() -> None:
    snapshot = _snapshot()
    text = _dashboard_text(snapshot, TuiState())
    assert "No sessions yet." in text
    assert "No session selected." in text


@pytest.mark.asyncio
async def test_run_tui_app_quits_via_injected_keys_and_rebuilds_on_selection() -> None:
    snapshot = _two_session_snapshot()
    builds: list[str | None] = []

    async def builder(session_id: str | None) -> TuiSnapshot:
        builds.append(session_id)
        return snapshot

    keys = iter(["down", "quit"])

    def key_source(_timeout: float) -> str | None:
        return next(keys, "q")

    console = Console(file=StringIO(), force_terminal=False, width=120)
    status = await run_tui_app(
        None,
        snapshot_builder=builder,
        key_source=key_source,
        console=console,
    )

    assert status == 0
    assert builds == [None, SessionId("s-2")]


@pytest.mark.asyncio
async def test_run_tui_app_refresh_key_triggers_rebuild() -> None:
    snapshot = _two_session_snapshot()
    builds: list[str | None] = []

    async def builder(session_id: str | None) -> TuiSnapshot:
        builds.append(session_id)
        return snapshot

    keys = iter(["refresh", "quit"])

    def key_source(_timeout: float) -> str | None:
        return next(keys, "q")

    status = await run_tui_app(
        None,
        snapshot_builder=builder,
        key_source=key_source,
        console=Console(file=StringIO(), force_terminal=False, width=120),
    )

    assert status == 0
    assert builds == [None, "s-1"]
