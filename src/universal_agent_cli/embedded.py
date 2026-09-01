"""Embedded runtime launcher for the CLI.

Production CLI runs without ``--api-url`` talk to a locally spawned agentd
subprocess over HTTP instead of importing kernel internals: the client package
stays a pure API client, and the runtime runs in its own process.

Tests that need an in-process runtime keep injecting a ``RuntimeService``
directly; the embedded launcher is the production path.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path


class EmbeddedRuntimeError(RuntimeError):
    """Raised when the embedded agentd subprocess fails to start."""


class EmbeddedRuntime:
    """A running embedded agentd subprocess and its base URL."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        base_url: str,
        port_file: Path,
    ) -> None:
        self.process = process
        self.base_url = base_url
        self._port_file = port_file

    def shutdown(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self._port_file.unlink(missing_ok=True)


def launch_embedded_runtime(
    profile_config: str | None = None,
    *,
    timeout_seconds: float = 20.0,
) -> EmbeddedRuntime:
    """Spawn the kernel's agentd server and wait for its bound port."""

    handle, port_file_name = tempfile.mkstemp(prefix="universal-agent-agentd-", suffix=".port")
    Path(port_file_name).unlink(missing_ok=True)
    port_file = Path(port_file_name)
    command = [
        sys.executable,
        "-m",
        "universal_agent.agentd",
        "--host",
        "127.0.0.1",
        "--port",
        "0",
        "--port-file",
        str(port_file),
    ]
    if profile_config is not None:
        command.extend(("--profile-config", profile_config))

    process = subprocess.Popen(command)
    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise EmbeddedRuntimeError(
                    f"embedded agentd exited during startup with code {process.returncode}"
                )
            try:
                port = int(port_file.read_text(encoding="utf-8").strip())
            except (FileNotFoundError, ValueError):
                time.sleep(0.05)
                continue
            return EmbeddedRuntime(process, f"http://127.0.0.1:{port}", port_file)
        raise EmbeddedRuntimeError(
            f"embedded agentd did not report its port within {timeout_seconds} seconds"
        )
    except BaseException:
        if process.poll() is None:
            process.terminate()
        port_file.unlink(missing_ok=True)
        raise


__all__ = ["EmbeddedRuntime", "EmbeddedRuntimeError", "launch_embedded_runtime"]
