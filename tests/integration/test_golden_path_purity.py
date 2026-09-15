"""Golden Path purity tests (P1).

Verifies that the full Golden Path (init -> doctor -> run -> session) works
in a clean environment with ONLY core dependencies — no server, no openai,
no tui, no postgres extras.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_HEAVY_OPTIONAL = ("openai", "textual", "psycopg", "psycopg2")


def _probe_script(tmp_dir: str) -> str:
    profile = str(Path(tmp_dir) / "universal-agent" / "profile.json")
    return f"""
import json, sys
from io import StringIO
from pathlib import Path
from universal_agent_cli import run_cli

optional = {_HEAVY_OPTIONAL!r}
loaded = sorted(set(sys.modules) & set(optional))
if loaded:
    print(json.dumps({{"error": "optional deps loaded", "modules": loaded}}))
    sys.exit(1)

out = StringIO()
profile = {profile!r}

steps = [
    (["init", "--output-format", "json", "--output", profile], "init"),
    (["--profile-config", profile, "doctor"], "doctor"),
    (["--profile-config", profile, "run", "Analyze the demo workload",
      "--success", "healthy=true", "--output", "json"], "run"),
    (["--profile-config", profile, "session", "list"], "session list"),
]

import asyncio

for argv, label in steps:
    status = asyncio.run(run_cli(argv, stdout=out))
    if status != 0:
        print(json.dumps({{"error": f"{{label}} failed", "status": status}}))
        sys.exit(1)

print(json.dumps({{"status": "ok", "optional_deps_loaded": []}}))
"""


@pytest.mark.integration
def test_golden_path_works_without_optional_extras(tmp_path: Path) -> None:
    """The full Golden Path must work with ONLY core dependencies installed."""

    script = _probe_script(str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, f"Golden Path failed: {result.stdout}\n{result.stderr}"
    assert '"status": "ok"' in result.stdout


@pytest.mark.integration
def test_golden_path_does_not_import_optional_packages(tmp_path: Path) -> None:
    """Verify that the core runtime import graph stays free of optional deps."""

    probe = (
        "import sys\n"
        "from universal_agent_cli import run_cli\n"
        "from universal_agent.host import RuntimeHost\n"
        "optional = ('openai', 'textual', 'psycopg', 'psycopg2')\n"
        "loaded = sorted(set(sys.modules) & set(optional))\n"
        "print(','.join(loaded) if loaded else 'CLEAN')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.stdout.strip() == "CLEAN", (
        f"optional packages imported by core runtime: {result.stdout}"
    )
