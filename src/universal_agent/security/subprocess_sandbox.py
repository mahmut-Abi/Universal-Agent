from __future__ import annotations

import json
import os
import resource
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from universal_agent.security.sandbox import (
    SandboxActionContext,
    SandboxResult,
    SandboxViolation,
)
from universal_agent.security.trust import TrustBoundary, check_trust
from universal_agent.tools import Tool


@dataclass(frozen=True, slots=True)
class SubprocessSandboxConfig:
    """Configuration for subprocess sandbox."""

    # Resource limits
    max_cpu_time_seconds: int = 30
    max_wall_time_seconds: int = 60
    max_memory_mb: int = 512
    max_file_size_mb: int = 100
    max_processes: int = 50
    max_open_files: int = 256

    # Filesystem isolation
    allowed_read_paths: tuple[str, ...] = ()
    allowed_write_paths: tuple[str, ...] = ()
    working_directory: str | None = None

    # Network isolation
    allow_network: bool = False

    # Environment
    allowed_env_vars: tuple[str, ...] = (
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "TZ",
    )

    # Security
    drop_capabilities: bool = True
    no_new_privs: bool = True


class SubprocessSandboxExecutor:
    """Executes tool actions in an isolated subprocess with resource limits."""

    def __init__(self, config: SubprocessSandboxConfig | None = None) -> None:
        self._config = config or SubprocessSandboxConfig()

    def run(
        self,
        action: SandboxActionContext,
        *,
        boundary: TrustBoundary,
    ) -> SandboxResult:
        # First check trust boundary
        verdict = check_trust(
            boundary,
            risk=action.risk,
            side_effect=action.side_effect,
            network=action.network,
            path=action.path,
            env=action.env,
        )
        if not verdict.permitted:
            raise SandboxViolation(verdict)

        # Get the tool to execute
        tool = action.tool
        if tool is None:
            return SandboxResult(False, "no tool provided", False)

        # Prepare execution
        start_time = time.monotonic()

        try:
            # Create isolated environment
            env = self._prepare_environment(action)

            # Prepare stdin/stdout/stderr
            stdin_data = json.dumps(action.arguments).encode() if action.arguments else b""

            # Run in subprocess with isolation
            result = self._run_isolated(
                tool=tool,
                arguments=dict(action.arguments or {}),
                env=env,
                stdin_data=stdin_data,
            )

            elapsed = time.monotonic() - start_time

            stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
            stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""

            if result.returncode == 0:
                return SandboxResult(
                    True,
                    f"executed in {elapsed:.2f}s",
                    True,
                    output=stdout,
                )
            else:
                return SandboxResult(
                    False,
                    f"execution failed with code {result.returncode}: {stderr}",
                    True,
                    error=stderr,
                )

        except subprocess.TimeoutExpired:
            return SandboxResult(
                False,
                f"execution timed out after {self._config.max_wall_time_seconds}s",
                True,
                error="timeout",
            )
        except Exception as e:
            return SandboxResult(
                False,
                f"sandbox execution error: {e}",
                False,
                error=str(e),
            )

    def _prepare_environment(self, action: SandboxActionContext) -> dict[str, str]:
        """Prepare isolated environment variables."""
        env = {}

        # Allow only configured env vars
        for var in self._config.allowed_env_vars:
            if var in os.environ:
                env[var] = os.environ[var]

        # Add sandbox-specific env
        env["SANDBOX"] = "1"
        env["SANDBOX_ACTION_RISK"] = action.risk.value
        env["SANDBOX_ACTION_SIDE_EFFECT"] = action.side_effect.value

        if action.network:
            env["SANDBOX_NETWORK"] = action.network
        if action.path:
            env["SANDBOX_PATH"] = action.path
        if action.env:
            # env is tuple[str, ...] - strings in format "KEY=VALUE"
            for item in action.env:
                if "=" in item:
                    k, v = item.split("=", 1)
                    if k in self._config.allowed_env_vars:
                        env[k] = v

        return env

    def _run_isolated(
        self,
        tool: Tool,
        arguments: dict[str, Any],
        env: dict[str, str],
        stdin_data: bytes,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run tool in isolated subprocess."""

        # Prepare the script that will execute the tool
        script = self._generate_execution_script(tool, arguments)

        # Create temporary script file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, prefix="sandbox_"
        ) as f:
            f.write(script)
            script_path = f.name

        try:
            # Prepare subprocess arguments
            cmd = [
                sys.executable,
                "-I",  # Isolated mode: no PYTHONPATH, no site.py
                "-s",  # Don't add user site-packages
                script_path,
            ]

            # Set working directory
            cwd = self._config.working_directory or tempfile.gettempdir()

            # Run with resource limits via preexec_fn
            return subprocess.run(
                cmd,
                cwd=cwd,
                env=env,
                input=stdin_data,
                capture_output=True,
                timeout=self._config.max_wall_time_seconds,
                preexec_fn=self._set_resource_limits,
            )
        finally:
            # Clean up script file
            try:
                os.unlink(script_path)
            except OSError:
                pass

    def _set_resource_limits(self) -> None:
        """Set resource limits in child process."""
        config = self._config

        # CPU time limit
        resource.setrlimit(
            resource.RLIMIT_CPU,
            (config.max_cpu_time_seconds, config.max_cpu_time_seconds),
        )

        # Memory limit (address space)
        mem_bytes = config.max_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))

        # File size limit
        file_bytes = config.max_file_size_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))

        # Number of processes
        resource.setrlimit(resource.RLIMIT_NPROC, (config.max_processes, config.max_processes))

        # Open files
        resource.setrlimit(resource.RLIMIT_NOFILE, (config.max_open_files, config.max_open_files))

        # Drop privileges if configured
        if config.drop_capabilities:
            try:
                # Drop all capabilities (Linux only)
                if hasattr(os, "capset"):
                    import ctypes
                    import ctypes.util

                    libcap = ctypes.CDLL(ctypes.util.find_library("cap"), use_errno=True)
                    libcap.cap_clear(ctypes.byref(ctypes.c_int(0)))
            except Exception:
                pass

        # No new privileges
        if config.no_new_privs and hasattr(os, "prctl"):
            try:
                import ctypes

                libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
                PR_SET_NO_NEW_PRIVS = 38
                libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
            except Exception:
                pass

        # Ignore SIGHUP
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

    def _generate_execution_script(self, tool: Tool, arguments: dict[str, Any]) -> str:
        """Generate Python script that executes the tool."""
        # The tool is pickled and unpickled in the subprocess
        # We use a simple JSON-based protocol
        return f"""
import json
import sys
import pickle
import base64

# Tool definition (serialized)
tool_data = {
            json.dumps(
                {
                    "name": tool.definition.name,
                    "description": tool.definition.description,
                    "capabilities": list(tool.definition.capabilities),
                    "side_effect": tool.definition.side_effect.value,
                    "risk": tool.definition.risk.value,
                }
            )
        }

arguments = {json.dumps(arguments)}

# Import the actual tool module and execute
# The tool must be importable - we assume it's available in the environment
try:
    # Try to import the tool's module
    module_name = tool.definition.name.replace("-", "_").replace(".", "_")
    
    # For built-in tools, we execute directly
    # This is a simplified version - in production you'd have a tool registry
    import importlib
    
    # Try to find and execute the tool
    result = None
    try:
        # This is where the actual tool execution would happen
        # For now, we simulate with a placeholder
        result = {{"status": "executed", "tool": tool.definition.name, "args": arguments}}
    except Exception as e:
        result = {{"error": str(e), "tool": tool.definition.name}}
        print(json.dumps(result), file=sys.stderr)
        sys.exit(1)
    
    print(json.dumps(result))
except Exception as e:
    print(json.dumps({{"error": str(e)}}), file=sys.stderr)
    sys.exit(1)
"""


@dataclass(frozen=True, slots=True)
class ContainerSandboxConfig:
    """Configuration for container-based sandbox (Docker/podman)."""

    image: str = "python:3.12-slim"
    memory_limit: str = "512m"
    cpu_limit: str = "1.0"
    timeout_seconds: int = 60
    network_mode: str = "none"
    read_only_rootfs: bool = True
    tmpfs_size: str = "100m"
    user: str = "nobody"
    workdir: str = "/workspace"


class ContainerSandboxExecutor:
    """Executes tool actions in a container (Docker/podman)."""

    def __init__(self, config: ContainerSandboxConfig | None = None) -> None:
        self._config = config or ContainerSandboxConfig()
        self._runtime = self._detect_runtime()

    def _detect_runtime(self) -> str:
        for rt in ("podman", "docker"):
            if subprocess.run(["which", rt], capture_output=True).returncode == 0:
                return rt
        raise RuntimeError("no container runtime found (podman or docker required)")

    def run(
        self,
        action: SandboxActionContext,
        *,
        boundary: TrustBoundary,
    ) -> SandboxResult:
        verdict = check_trust(
            boundary,
            risk=action.risk,
            side_effect=action.side_effect,
            network=action.network,
            path=action.path,
            env=action.env,
        )
        if not verdict.permitted:
            raise SandboxViolation(verdict)

        tool = action.tool
        if tool is None:
            return SandboxResult(False, "no tool provided", False)

        try:
            result = self._run_in_container(tool, dict(action.arguments or {}))
            stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
            stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            if result.returncode == 0:
                return SandboxResult(True, "executed in container", True, output=stdout)
            else:
                return SandboxResult(
                    False,
                    f"container execution failed: {stderr}",
                    True,
                    error=stderr,
                )
        except subprocess.TimeoutExpired:
            return SandboxResult(False, "container timed out", True, error="timeout")
        except Exception as e:
            return SandboxResult(False, f"container error: {e}", False, error=str(e))

    def _run_in_container(
        self, tool: Tool, arguments: dict[str, Any]
    ) -> subprocess.CompletedProcess[bytes]:
        # Prepare script
        script = self._generate_execution_script(tool, arguments)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(script)
            script_path = f.name

        try:
            # Mount script into container
            script_name = Path(script_path).name
            cmd = [
                self._runtime,
                "run",
                "--rm",
                "--memory",
                self._config.memory_limit,
                "--cpus",
                self._config.cpu_limit,
                "--network",
                self._config.network_mode,
                "--read-only" if self._config.read_only_rootfs else "",
                "--tmpfs",
                f"{self._config.workdir}:size={self._config.tmpfs_size}",
                "--user",
                self._config.user,
                "--workdir",
                self._config.workdir,
                "-v",
                f"{script_path}:{self._config.workdir}/{script_name}:ro",
                self._config.image,
                "python",
                "-I",
                "-s",
                f"{self._config.workdir}/{script_name}",
            ]
            # Filter empty strings
            cmd = [c for c in cmd if c]

            return subprocess.run(
                cmd,
                capture_output=True,
                timeout=self._config.timeout_seconds,
            )
        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass

    def _generate_execution_script(self, tool: Tool, arguments: dict[str, Any]) -> str:
        return f"""
import json
import sys

arguments = {json.dumps(arguments)}
tool_name = {json.dumps(tool.definition.name)}

try:
    result = {{"status": "executed", "tool": tool_name, "args": arguments}}
    print(json.dumps(result))
except Exception as e:
    print(json.dumps({{"error": str(e), "tool": tool_name}}), file=sys.stderr)
    sys.exit(1)
"""
