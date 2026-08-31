"""Interactive full-screen TUI for the Universal Agent Runtime, built on Textual.

The dashboard lists runtime sessions in a navigable table and shows the
selected session's detail (goal, task, termination, evidence/world counts and
a recent-event tail). All data comes from the same read-only
``build_tui_snapshot`` projections the static TUI and web console use, so the
three surfaces stay consistent. Operator actions (pause/resume/confirm/cancel)
dispatch through :class:`TuiActions` — the same RuntimeService methods the CLI
and agentd use — so policy and confirmation boundaries stay identical across
surfaces. ``snapshot_provider`` and ``actions`` are injection seams for
headless pilot tests; the CLI path uses the real RuntimeService-backed
provider (embedded) or the agentd HTTP client (remote).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime  # noqa: F401  (re-exported typing aid for tests)
from types import MappingProxyType  # noqa: F401
from typing import Any, ClassVar

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from universal_agent.core import GoalStatus, SessionId
from universal_agent.runtime import SessionSummaryView, SessionView
from universal_agent.service import RuntimeService
from universal_agent.terminal.tui import TuiSnapshot, build_tui_snapshot

SnapshotProvider = Callable[[SessionId | None], Awaitable[TuiSnapshot]]


@dataclass(frozen=True)
class TuiActions:
    """Operator action surface: embedded RuntimeService or remote agentd client."""

    pause: Callable[[SessionId, str | None], Awaitable[object]]
    resume: Callable[[SessionId, bool | None], Awaitable[object]]
    cancel: Callable[[SessionId, str | None], Awaitable[object]]


def service_tui_actions(service: RuntimeService) -> TuiActions:
    """Operator actions backed by the in-process RuntimeService."""

    async def pause(session_id: SessionId, reason: str | None) -> object:
        return await service.pause_session(session_id, reason=reason or "session paused")

    async def resume(session_id: SessionId, confirmed: bool | None) -> object:
        return await service.resume_session(session_id, confirmed=confirmed)

    async def cancel(session_id: SessionId, reason: str | None) -> object:
        return await service.cancel_session(session_id, reason=reason or "session cancelled")

    return TuiActions(pause=pause, resume=resume, cancel=cancel)


_EVENT_TAIL = 8
_KEY_HINTS = (
    "j/k or arrows: select · enter: resume/confirm · "
    "p: pause · c: cancel · r: refresh · q: quit"
)
_SELECTED_STYLE = "bold cyan"


def _service_provider(
    service: RuntimeService | None,
    *,
    session_limit: int,
    event_limit: int,
) -> SnapshotProvider:
    async def provider(session_id: SessionId | None) -> TuiSnapshot:
        if service is None:
            raise ValueError(
                "RuntimeTuiApp requires a service when no snapshot_provider is given"
            )
        return await build_tui_snapshot(
            service,
            session_id=session_id,
            session_limit=session_limit,
            event_limit=event_limit,
        )

    return provider


def session_table_rows(snapshot: TuiSnapshot) -> list[tuple[str, str, str]]:
    """Pure projection: one (session, status, goal+task) row per session."""

    return [
        (
            str(summary.session_id),
            summary.goal_status.value,
            f"{summary.goal_description} — {summary.current_task_description}",
        )
        for summary in snapshot.sessions
    ]


def session_detail_lines(
    snapshot: TuiSnapshot,
    index: int,
    summary: SessionSummaryView | SessionView,
) -> list[str]:
    """Pure projection: plain detail lines for the selected session row."""

    lines = [
        f"session: {summary.session_id}",
        f"goal: {summary.goal_status.value} {summary.goal_description}",
        f"task: {summary.current_task_status.value} "
        f"{summary.current_task_description} (iteration {summary.iteration})",
    ]
    if isinstance(summary, SessionView):
        if summary.termination_reason:
            lines.append(f"termination: {summary.termination_reason}")
        if summary.error_code:
            lines.append(f"error: {summary.error_code.value}")
    explorer = snapshot.session_explorer
    if explorer is not None:
        lines.append(
            f"evidence={len(explorer.evidence)} "
            f"world_facts={len(explorer.world_facts)} "
            f"entities={len(explorer.world_entities)} "
            f"relations={len(explorer.world_relations)}"
        )
    lines.append("")
    lines.append("Recent events")
    from universal_agent.terminal.tui import _event_lines

    event_lines = _event_lines(snapshot.events)
    lines.extend(event_lines[-_EVENT_TAIL:] if _EVENT_TAIL else event_lines)
    return lines


class RuntimeTuiApp(App[None]):
    """Live runtime dashboard driven by RuntimeService snapshot projections."""

    TITLE = "Universal Agent Runtime TUI"

    CSS = """
    #sessions-table {
        width: 2fr;
        height: 100%;
    }
    #detail {
        width: 3fr;
        height: 100%;
        padding: 0 1;
    }
    #reason-grid, #choice-grid {
        padding: 1 2;
        width: 60;
        height: auto;
        margin: 2;
        border: solid $accent;
        background: $surface;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("j,down", "select_next", "Next session", show=False),
        Binding("k,up", "select_previous", "Previous session", show=False),
        Binding("g,home", "select_first", "First session", show=False),
        Binding("G,end", "select_last", "Last session", show=False),
        Binding("r", "refresh", "Refresh now"),
        Binding("p", "pause_session", "Pause", show=False),
        Binding("c", "cancel_session", "Cancel", show=False),
        Binding("enter", "resume_or_confirm", "Resume / confirm", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        service: RuntimeService | None = None,
        *,
        session_id: SessionId | None = None,
        session_limit: int = 5,
        event_limit: int = 12,
        refresh_seconds: float = 2.0,
        snapshot_provider: SnapshotProvider | None = None,
        actions: TuiActions | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._session_id = session_id
        self._session_limit = session_limit
        self._event_limit = event_limit
        self._refresh_seconds = refresh_seconds
        self._snapshot: TuiSnapshot | None = None
        self._selected_index = 0
        self._hint: str | None = None
        self._restoring = False
        self._refresh_task: asyncio.Task[None] | None = None
        self._actions = actions
        self._table: DataTable[Any] = DataTable(id="sessions-table")
        self._detail = Static("", id="detail")
        self._provider: SnapshotProvider = snapshot_provider or _service_provider(
            service, session_limit=session_limit, event_limit=event_limit
        )

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield self._table
            yield self._detail
        yield Footer()

    def on_mount(self) -> None:
        self._table.cursor_type = "row"
        self._table.add_columns("session", "status", "goal")
        self.set_interval(self._refresh_seconds, self.action_refresh)
        self.call_after_refresh(self.action_refresh)

    def action_refresh(self) -> None:
        """Spawn an exclusive snapshot refresh, cancelling any in-flight one."""

        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = asyncio.create_task(self._refresh_snapshot())

    async def _refresh_snapshot(self) -> None:
        self._snapshot = await self._provider(self._current_session_id())
        self._sync_table()
        self._update_detail()

    def action_select_next(self) -> None:
        self._sessions_table().action_cursor_down()

    def action_select_previous(self) -> None:
        self._sessions_table().action_cursor_up()

    def action_select_first(self) -> None:
        self._move_table_cursor_to(0)

    def action_select_last(self) -> None:
        count = self._sessions_table().row_count
        if count:
            self._move_table_cursor_to(count - 1)

    def _sessions_table(self) -> DataTable[Any]:
        return self._table

    def _move_table_cursor_to(self, row: int) -> None:
        if row >= 0:
            self._sessions_table().move_cursor(row=row)

    def _session_count(self) -> int:
        return len(self._snapshot.sessions) if self._snapshot is not None else 0

    def _current_session_id(self) -> SessionId | None:
        if self._snapshot is None:
            return self._session_id
        if 0 <= self._selected_index < len(self._snapshot.sessions):
            return self._snapshot.sessions[self._selected_index].session_id
        return None

    @on(DataTable.RowHighlighted, "#sessions-table")
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self._restoring or event.cursor_row == self._selected_index:
            return  # programmatic restore or no-op move, not user navigation
        self._selected_index = event.cursor_row
        self._update_detail()
        self.action_refresh()

    def _sync_table(self) -> None:
        if self._snapshot is None:
            return
        self._restoring = True
        try:
            table = self._table
            table.clear()
            for session_id, status, goal in session_table_rows(self._snapshot):
                table.add_row(session_id, status, goal)
            count = self._session_count()
            if count:
                self._move_table_cursor_to(min(self._selected_index, count - 1))
        finally:
            self._restoring = False

    def _update_detail(self) -> None:
        if self._snapshot is None:
            return
        summary = self._selected_summary()
        if summary is None:
            self._detail.update(Text("No session selected.", style="dim"))
            return
        lines = session_detail_lines(self._snapshot, self._selected_index, summary)
        self._detail.update(Text("\n".join(lines)))

    def _selected_summary(self) -> SessionSummaryView | SessionView | None:
        if self._snapshot is None:
            return None
        if self._selected_index < len(self._snapshot.sessions):
            summary = self._snapshot.sessions[self._selected_index]
            selected = self._snapshot.selected_session
            if selected is not None and selected.session_id == summary.session_id:
                return selected
            return summary
        return self._snapshot.selected_session

    # ---- Operator actions ----

    def action_pause_session(self) -> None:
        self._push_reason_action("Pause session", "Pause reason", "pause")

    def action_cancel_session(self) -> None:
        self._push_reason_action("Cancel session", "Cancellation reason", "cancel")

    def action_resume_or_confirm(self) -> None:
        summary = self._selected_summary()
        if summary is None or self._actions is None:
            self._set_hint("operator actions require an embedded service or --api-url")
            return
        if summary.goal_status is not GoalStatus.WAITING:
            self._set_hint("resume requires a waiting session")
            return
        has_pending = (
            summary.pending_action is not None
            if isinstance(summary, SessionView)
            else bool(summary.pending_action)
        )
        if has_pending:
            session_id = self._current_session_id()

            def _choice(confirmed: bool | None) -> None:
                if confirmed is not None and session_id is not None:
                    self._run_operator("resume", confirmed)

            self.push_screen(
                TuiChoiceScreen(
                    "Confirm pending action",
                    "Confirm the pending action and resume the session?",
                ),
                _choice,
            )
            return
        self._run_operator("resume", None)

    def _push_reason_action(self, title: str, label: str, kind: str) -> None:
        def _submit(reason: str | None) -> None:
            if reason is None:
                return
            self._run_operator(kind, reason)

        self.push_screen(TuiReasonScreen(title, label), _submit)

    def _set_hint(self, message: str) -> None:
        self._hint = message
        self.sub_title = message

    @work(exclusive=True, exit_on_error=False)
    async def _run_operator(self, kind: str, payload: str | bool | None) -> None:
        if self._actions is None:
            self._set_hint("operator actions require an embedded service or --api-url")
            return
        session_id = self._current_session_id()
        if session_id is None:
            self._set_hint("no session selected")
            return
        if kind == "pause":
            await self._actions.pause(session_id, str(payload) if payload else None)
        elif kind == "resume":
            await self._actions.resume(
                session_id, payload if isinstance(payload, bool) else None
            )
        else:
            await self._actions.cancel(session_id, str(payload) if payload else None)
        self._set_hint(f"{kind} completed")
        self.action_refresh()


class TuiReasonScreen(ModalScreen["str | None"]):
    """Modal text-input screen collecting an optional operator reason."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "dismiss_modal", "Cancel")]

    def __init__(self, title: str, label: str) -> None:
        super().__init__()
        self._screen_title = title
        self._label = label

    def compose(self) -> ComposeResult:
        with Vertical(id="reason-grid"):
            yield Label(self._screen_title, id="reason-title")
            yield Label(self._label, id="reason-label")
            yield Input(placeholder="optional", id="reason-input")
            yield Label("enter submits · esc cancels", id="reason-hint")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


class TuiChoiceScreen(ModalScreen["bool | None"]):
    """Modal confirm/reject choice for a pending action."""

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "dismiss_modal", "Cancel")]

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self._screen_title = title
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="choice-grid"):
            yield Label(self._screen_title, id="choice-title")
            yield Label(self._message, id="choice-message")
            with Horizontal():
                yield Button("Confirm & resume", id="confirm", variant="success")
                yield Button("Reject", id="reject", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


__all__ = [
    "RuntimeTuiApp",
    "TuiActions",
    "service_tui_actions",
    "session_detail_lines",
    "session_table_rows",
]
