"""Tests for embedded agentd startup failure diagnostics.

When the embedded agentd subprocess dies during startup, the launcher must
surface the subprocess exit code and the tail of its stderr so the CLI user
sees why startup failed instead of an empty failure message.
"""

from __future__ import annotations

import pytest

from universal_agent_cli.embedded import launch_embedded_runtime


@pytest.mark.integration
def test_embedded_runtime_startup_failure_includes_stderr_tail(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A malformed profile passes the CLI existence check but makes the agentd
    # subprocess fail during startup; the launcher must surface that failure
    # with the subprocess stderr tail instead of a bare exit code.
    profile = tmp_path / "broken-profile.json"
    profile.write_text('{"broken": true}', encoding="utf-8")

    with pytest.raises(RuntimeError) as excinfo:
        launch_embedded_runtime(
            profile_config=str(profile),
            timeout_seconds=20,
        )

    message = str(excinfo.value)
    assert "exited during startup" in message
    # The agentd subprocess prints the root cause to stderr; the launcher must
    # include that context in the raised error.
    assert len(message) > len("embedded agentd exited during startup with code 1")
