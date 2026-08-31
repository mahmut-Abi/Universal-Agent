"""Terminal-facing compatibility and rendering exports."""

from universal_agent.terminal.console import RuntimeConsoleSnapshot, build_runtime_console_snapshot
from universal_agent.terminal.render import TerminalLineStyler, render_terminal_lines
from universal_agent.terminal.tui import TuiSnapshot, build_tui_snapshot, render_tui_snapshot
from universal_agent.terminal.tui_app import (
    RuntimeTuiApp,
    session_detail_lines,
    session_table_rows,
)

__all__ = [
    "RuntimeConsoleSnapshot",
    "RuntimeTuiApp",
    "TerminalLineStyler",
    "TuiSnapshot",
    "build_runtime_console_snapshot",
    "build_tui_snapshot",
    "render_terminal_lines",
    "render_tui_snapshot",
    "session_detail_lines",
    "session_table_rows",
]
