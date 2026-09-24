"""Shell and git backends for the code domain.

All commands run inside the workspace directory with a timeout. The backend
never resolves paths outside the workspace for git operations.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

_DEFAULT_TIMEOUT = 30.0
_MAX_OUTPUT_BYTES = 64 * 1024  # 64 KB


class CommandResult:
    __slots__ = ("exit_code", "stderr", "stdout", "timed_out")

    def __init__(
        self,
        *,
        exit_code: int,
        stdout: str,
        stderr: str,
        timed_out: bool = False,
    ) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_BYTES:
        return text
    return text[:_MAX_OUTPUT_BYTES] + "\n...[truncated]"


class ShellBackend:
    """Executes shell commands inside a workspace directory with timeout."""

    def __init__(self, workspace_path: str, *, timeout_seconds: float = _DEFAULT_TIMEOUT) -> None:
        self._workspace = Path(workspace_path).resolve()
        self._timeout_seconds = timeout_seconds

    @property
    def workspace(self) -> Path:
        return self._workspace

    async def run(self, command: str, *, timeout_seconds: float | None = None) -> CommandResult:
        """Execute a shell command with cwd set to the workspace."""
        timeout = timeout_seconds if timeout_seconds is not None else self._timeout_seconds
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self._workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "TERM": "dumb"},
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr=f"command timed out after {timeout}s",
                timed_out=True,
            )
        return CommandResult(
            exit_code=process.returncode or 0,
            stdout=_truncate(stdout.decode("utf-8", errors="replace")),
            stderr=_truncate(stderr.decode("utf-8", errors="replace")),
        )

    async def git(self, *args: str, timeout_seconds: float | None = None) -> CommandResult:
        """Run a git command inside the workspace."""
        return await self.run(f"git {' '.join(args)}", timeout_seconds=timeout_seconds)
