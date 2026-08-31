"""Interactive full-screen TUI application for the Universal Agent Runtime.

The dashboard re-renders on a cadence (or on demand) from fresh RuntimeService
snapshots while a small POSIX keyboard reader feeds navigation keys. The pure
state transition (:func:`handle_key`) and the layout builder
(:func:`build_dashboard`) are separately unit-testable; terminal I/O is
isolated in :class:`TerminalKeyReader`. On platforms without ``termios`` the
runner falls back to the deterministic one-shot snapshot render.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from types import MappingProxyType, TracebackType
from typing import Any, TextIO

from rich.console import Console, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from universal_agent.core import SessionId
from universal_agent.runtime import RuntimeEventView, SessionSummaryView, SessionView
from universal_agent.service import ReadyView, RuntimeService
from universal_agent.terminal.tui import TuiSnapshot, build_tui_snapshot

try:  # pragma: no cover - POSIX-only imports
    import select
    import termios
    import tty

    _KEY_READER_AVAILABLE = True
except ImportError:  # pragma: no cover - Windows fallback
    select = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]
    _KEY_READER_AVAILABLE = False

KeySource = Callable[[float], "str | None"]
SnapshotBuilder = Callable[["SessionId | None"], Awaitable[TuiSnapshot]]

_KEY_HINTS = "j/k or arrows: select · g/G: first/last · r: refresh now · q: quit"
_SELECTED_STYLE = "bold cyan"


@dataclass(frozen=True, slots=True)
class TuiState:
    """Immutable UI state advanced purely by :func:`handle_key`."""

    selected_index: int = 0
    quit_requested: bool = False
    refresh_requested: bool = False
    status_hint: str | None = None


def map_key(raw: str) -> str | None:
    """Map a raw terminal key (or escape sequence) to a logical action name."""

    sequences = {
        "\x1b[A": "up",
        "\x1b[B": "down",
        "\x1b[H": "home",
        "\x1b[F": "end",
        "\x1b[1~": "home",
        "\x1b[4~": "end",
    }
    if raw in sequences:
        return sequences[raw]
    singles = {
        "j": "down",
        "k": "up",
        "g": "home",
        "G": "end",
        "r": "refresh",
        "q": "quit",
        "Q": "quit",
    }
    if raw in singles:
        return singles[raw]
    return None


def handle_key(state: TuiState, key: str | None, session_count: int) -> TuiState:
    """Return the next state for a logical key press (pure transition)."""

    if key is None:
        return state
    if key == "quit":
        return replace(state, quit_requested=True)
    if key == "refresh":
        return replace(state, refresh_requested=True, status_hint="refreshing…")
    if key in ("up", "down", "home", "end"):
        if session_count == 0:
            return replace(state, status_hint="no sessions to select")
        index = state.selected_index
        if key == "down":
            index = (index + 1) % session_count
        elif key == "up":
            index = (index - 1) % session_count
        elif key == "home":
            index = 0
        else:
            index = session_count - 1
        return replace(state, selected_index=index, status_hint=None)
    return replace(state, status_hint=f"unmapped key: {key!r}")


def selected_session_id(snapshot: TuiSnapshot, state: TuiState) -> SessionId | None:
    """Map the current selection index onto a concrete session id."""

    if state.selected_index < len(snapshot.sessions):
        return snapshot.sessions[state.selected_index].session_id
    return None


def build_dashboard(snapshot: TuiSnapshot, state: TuiState) -> RenderableType:
    """Compose the full-screen dashboard layout for the current state."""

    layout = Table.grid(padding=(0, 1))
    layout.add_column()
    layout.add_row(_header_text(snapshot))
    layout.add_row(_body_columns(snapshot, state))
    layout.add_row(_footer_text(state))
    return layout


def _header_text(snapshot: TuiSnapshot) -> Text:
    header = Text()
    header.append("Universal Agent Runtime TUI", style="bold")
    header.append("\n")
    header.append(
        f"health={snapshot.health.status} ready={_ready_text(snapshot.ready)} "
        f"sessions={snapshot.metrics.session_count} "
        f"active={snapshot.metrics.active_session_count} "
        f"waiting={snapshot.metrics.waiting_session_count} "
        f"events={snapshot.metrics.event_count}",
        style="dim",
    )
    header.append("\n")
    header.append(
        f"cost calls={snapshot.cost.model_call_count} "
        f"tokens={snapshot.cost.total_tokens} "
        f"micros={snapshot.cost.estimated_cost_micros} {snapshot.cost.currency}",
        style="dim",
    )
    return header


def _ready_text(ready: ReadyView) -> str:
    return "yes" if ready.ready else "no"


def _body_columns(snapshot: TuiSnapshot, state: TuiState) -> RenderableType:
    columns = Table.grid(padding=(1, 1))
    columns.add_column(ratio=2)
    columns.add_column(ratio=3)
    columns.add_row(
        Panel(_sessions_table(snapshot, state), title="Sessions (j/k)"),
        Panel(_detail_text(snapshot, state), title="Selected session"),
    )
    return columns


def _sessions_table(snapshot: TuiSnapshot, state: TuiState) -> RenderableType:
    if not snapshot.sessions:
        return Text("No sessions yet.", style="dim")
    table = Table.grid(padding=(0, 1))
    table.add_column(justify="right")
    table.add_column()
    table.add_column()
    for index, summary in enumerate(snapshot.sessions):
        selected = index == state.selected_index
        style = _SELECTED_STYLE if selected else ""
        marker = Text(">" if selected else " ", style=style)
        session_text = (
            Text(str(summary.session_id), style=style),
            Text(
                f"{summary.goal_status.value} {summary.goal_description}",
                style=style,
            ),
        )
        table.add_row(marker, *session_text)
    return table


def _detail_text(snapshot: TuiSnapshot, state: TuiState) -> RenderableType:
    lines: list[str] = []
    session = _selected_or_indexed(snapshot, state)
    if session is None:
        lines.append("No session selected.")
    elif isinstance(session, SessionView):
        lines.extend(_session_lines(session))
    else:
        lines.extend(_summary_lines(session))
    explorer = snapshot.session_explorer
    if explorer is not None:
        lines.append(
            f"evidence={len(explorer.evidence)} "
            f"world_facts={len(explorer.world_facts)} "
            f"audit={len(snapshot.audit_records)}"
        )
    lines.append("")
    lines.append("Recent events")
    lines.extend(_tail_lines(snapshot.events, 8))
    return Text("\n".join(lines))


def _session_lines(session: SessionView) -> list[str]:
    lines = _summary_lines(session)
    if session.termination_reason:
        lines.append(f"termination: {session.termination_reason}")
    if session.error_code:
        lines.append(f"error: {session.error_code.value}")
    return lines


def _summary_lines(
    session: SessionView | SessionSummaryView,
) -> list[str]:
    return [
        f"session: {session.session_id}",
        f"goal: {session.goal_status.value} {session.goal_description}",
        f"task: {session.current_task_status.value} "
        f"{session.current_task_description} (iteration {session.iteration})",
    ]


def _selected_or_indexed(
    snapshot: TuiSnapshot, state: TuiState
) -> SessionView | SessionSummaryView | None:
    """Prefer the full SessionView only when it matches the current selection."""

    if state.selected_index < len(snapshot.sessions):
        summary = snapshot.sessions[state.selected_index]
        selected = snapshot.selected_session
        if selected is not None and selected.session_id == summary.session_id:
            return selected
        return summary
    return snapshot.selected_session


def _tail_lines(events: tuple[RuntimeEventView, ...], limit: int) -> list[str]:
    from universal_agent.terminal.tui import _event_lines

    lines = _event_lines(events)
    return lines[-limit:] if limit else []


def _footer_text(state: TuiState) -> Text:
    footer = Text(_KEY_HINTS, style="dim")
    if state.status_hint:
        footer.append(f"  [{state.status_hint}]", style="yellow")
    return footer


class TerminalKeyReader:
    """Non-blocking POSIX keyboard reader in cbreak mode."""

    def __init__(self, input_stream: TextIO) -> None:
        self._stream = input_stream
        self._fd = input_stream.fileno()
        self._saved: list[Any] | None = None

    def __enter__(self) -> TerminalKeyReader:
        assert termios is not None and tty is not None, "POSIX-only reader"
        self._saved = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd, termios.TCSANOW)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._saved is not None:
            assert termios is not None, "POSIX-only reader"
            termios.tcsetattr(self._fd, termios.TCSANOW, self._saved)
            self._saved = None

    def poll(self, timeout: float) -> str | None:
        assert select is not None, "POSIX-only reader"
        ready, _, _ = select.select([self._fd], [], [], timeout)
        if not ready:
            return None
        raw = self._stream.read(1) or ""
        if raw == "\x1b":
            followup, _, _ = select.select([self._fd], [], [], 0.01)
            if followup:
                raw += self._stream.read(1) or ""
                extra, _, _ = select.select([self._fd], [], [], 0.01)
                if extra and raw in ("\x1b[1", "\x1b[4"):
                    raw += self._stream.read(1) or ""
        return map_key(raw)


async def run_tui_app(
    service: RuntimeService | None,
    *,
    session_id: SessionId | None = None,
    session_limit: int = 5,
    event_limit: int = 12,
    refresh_seconds: float = 2.0,
    key_source: KeySource | None = None,
    console: Console | None = None,
    snapshot_builder: SnapshotBuilder | None = None,
    input_stream: TextIO | None = None,
) -> int:
    """Run the interactive dashboard until the user quits.

    ``key_source`` and ``snapshot_builder`` are injection seams for tests; the
    CLI path uses the default terminal key reader and the real snapshot
    builder. On platforms without ``termios`` the runner degrades to the
    deterministic one-shot render and returns 0.
    """

    builder: SnapshotBuilder = snapshot_builder or _service_builder(
        service, session_limit=session_limit, event_limit=event_limit
    )
    snapshot = await builder(session_id)
    state = TuiState(selected_index=_initial_index(snapshot, session_id))
    current_session_id = session_id
    live_console = console or Console()
    reader: TerminalKeyReader | None = None

    if key_source is None and _KEY_READER_AVAILABLE and input_stream is not None:
        reader = TerminalKeyReader(input_stream)

    def default_poll(timeout: float) -> str | None:
        if reader is not None:
            return reader.poll(timeout)
        return None

    poll = key_source or default_poll

    with Live(
        build_dashboard(snapshot, state),
        console=live_console,
        screen=True,
        auto_refresh=False,
        refresh_per_second=4,
    ) as live:
        last_refresh = time.monotonic()
        while not state.quit_requested:
            key = poll(0.1)
            if key is not None:
                state = handle_key(state, key, len(snapshot.sessions))
            selection = selected_session_id(snapshot, state)
            now = time.monotonic()
            if (
                selection != current_session_id
                or state.refresh_requested
                or (now - last_refresh) >= refresh_seconds
            ):
                snapshot = await builder(selection)
                state = replace(state, refresh_requested=False)
                current_session_id = selection
                last_refresh = now
            live.update(build_dashboard(snapshot, state))
            if key_source is not None:
                await asyncio.sleep(0)
            else:
                await asyncio.sleep(0.05)
    return 0


def _service_builder(
    service: RuntimeService | None,
    *,
    session_limit: int,
    event_limit: int,
) -> SnapshotBuilder:
    async def build(selected: SessionId | None) -> TuiSnapshot:
        if service is None:
            raise ValueError("run_tui_app requires a service when no snapshot_builder is given")
        return await build_tui_snapshot(
            service,
            session_id=selected,
            session_limit=session_limit,
            event_limit=event_limit,
        )

    return build


def _initial_index(snapshot: TuiSnapshot, session_id: SessionId | None) -> int:
    if session_id is None:
        return 0
    for index, summary in enumerate(snapshot.sessions):
        if summary.session_id == session_id:
            return index
    return 0


_ = MappingProxyType  # re-exported typing aid; keep import stable for tests

__all__ = [
    "TerminalKeyReader",
    "TuiState",
    "build_dashboard",
    "handle_key",
    "map_key",
    "run_tui_app",
    "selected_session_id",
]
