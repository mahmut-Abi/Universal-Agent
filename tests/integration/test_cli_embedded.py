"""End-to-end coverage for the CLI's embedded runtime launcher.

Production CLI runs without ``--api-url`` or an injected service spawn the
kernel's agentd server in an isolated subprocess and talk to it over HTTP —
the client package itself stays a pure API client.
"""

from __future__ import annotations

import json
import urllib.request
from io import StringIO

import pytest

from universal_agent_cli import run_cli
from universal_agent_cli.embedded import launch_embedded_runtime


@pytest.mark.integration
def test_embedded_runtime_serves_health_over_http() -> None:
    embedded = launch_embedded_runtime()
    try:
        with urllib.request.urlopen(embedded.base_url + "/health", timeout=5) as response:
            body = json.load(response)
        assert body == {"status": "ok", "service": "universal-agent-runtime"}
    finally:
        embedded.shutdown()

    assert embedded.process.poll() is not None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cli_production_path_runs_commands_through_embedded_runtime() -> None:
    output = StringIO()

    status = await run_cli(["health"], stdout=output)
    payload = json.loads(output.getvalue())

    assert status == 0
    assert payload == {"status": "ok", "service": "universal-agent-runtime"}
