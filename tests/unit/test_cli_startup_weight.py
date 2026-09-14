"""CLI startup-weight contract tests.

The CLI package must stay import-light: building the parser (the floor for
every command, including `ua --help`) must not pull the server, persistence,
or observability stacks. These tests fail if someone reintroduces an eager
import of a heavyweight dependency into the CLI import graph.
"""

from __future__ import annotations

import subprocess
import sys

HEAVY_ROOTS = (
    "starlette",
    "uvicorn",
    "sqlalchemy",
    "prometheus_client",
    "opentelemetry",
    "jinja2",
    "rapidfuzz",
    "tenacity",
    "rich",
    "httpx",
    "yaml",
    "junit_xml",
    "jsonlines",
    "filelock",
)

_PROBE = """
import sys
{target}
loaded = sorted(set(sys.modules) & set({roots!r}))
print(",".join(loaded))
"""


def _loaded_heavy_roots(target: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE.format(target=target, roots=HEAVY_ROOTS)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip().split(",") if result.stdout.strip() else []


def test_cli_package_import_stays_light() -> None:
    assert _loaded_heavy_roots("import universal_agent_cli") == []


def test_cli_parser_import_stays_light() -> None:
    assert _loaded_heavy_roots("import universal_agent_cli.parser") == []


def test_parser_local_profile_name_matches_kubernetes_domain() -> None:
    """Contract guard: the parser constant is the Kubernetes Domain's value."""

    from universal_agent.domains.kubernetes.cli import LOCAL_PROFILE_NAME as via_cli
    from universal_agent.domains.kubernetes.cli_parser import LOCAL_PROFILE_NAME
    from universal_agent_cli.parser import LOCAL_PROFILE_NAME as via_parser

    assert via_parser == via_cli == LOCAL_PROFILE_NAME
