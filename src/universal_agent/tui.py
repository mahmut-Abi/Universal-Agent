"""Backward-compatible import shim for the terminal UI projection."""

from universal_agent_tui.tui import TuiSnapshot, build_tui_snapshot, render_tui_snapshot

__all__ = ["TuiSnapshot", "build_tui_snapshot", "render_tui_snapshot"]
