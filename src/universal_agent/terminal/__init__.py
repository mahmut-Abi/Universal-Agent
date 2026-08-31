"""Terminal-facing compatibility and rendering exports."""

from universal_agent.terminal.console import RuntimeConsoleSnapshot, build_runtime_console_snapshot
from universal_agent.terminal.render import TerminalLineStyler, render_terminal_lines
from universal_agent.terminal.tui import TuiSnapshot, build_tui_snapshot, render_tui_snapshot
from universal_agent.terminal.tui_app import (
    TuiState,
    build_dashboard,
    handle_key,
    map_key,
    run_tui_app,
    selected_session_id,
)

__all__ = [
    "RuntimeConsoleSnapshot",
    "TerminalKeyReader",
    "TerminalLineStyler",
    "TuiSnapshot",
    "TuiState",
    "build_dashboard",
    "build_runtime_console_snapshot",
    "build_tui_snapshot",
    "handle_key",
    "map_key",
    "render_terminal_lines",
    "render_tui_snapshot",
    "run_tui_app",
    "selected_session_id",
]
