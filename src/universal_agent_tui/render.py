from __future__ import annotations

from collections.abc import Callable, Iterable
from io import StringIO

from rich.console import Console
from rich.text import Text

TerminalLineStyler = Callable[[str], str | None]


def render_terminal_lines(
    lines: Iterable[str],
    *,
    styler: TerminalLineStyler | None = None,
    width: int = 240,
) -> str:
    """Render deterministic terminal text through Rich without ANSI output."""

    buffer = StringIO()
    console = Console(
        file=buffer,
        force_terminal=False,
        color_system=None,
        highlight=False,
        width=width,
    )
    for line in lines:
        style = None if styler is None else styler(line)
        text = Text(line) if style is None else Text(line, style=style)
        console.print(
            text,
            markup=False,
            highlight=False,
            soft_wrap=True,
        )
    return buffer.getvalue()


__all__ = ["TerminalLineStyler", "render_terminal_lines"]
