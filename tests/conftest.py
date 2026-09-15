"""Shared test fixtures.

The P0 Golden Path CLI discovers the profile config from ``AGENT_CONFIG_DIR``
(or the user home). Tests must stay hermetic against the developer machine's
real ``~/.universal-agent`` state, so every test runs with machine-local config
and data directories pointed at empty temp paths.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _hermetic_agent_dirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    monkeypatch.setenv("AGENT_CONFIG_DIR", str(tmp_path_factory.mktemp("agent-config")))
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path_factory.mktemp("agent-data")))
    # Scaffold stubs are allowed by default in tests; the production loading
    # guard tests explicitly opt out (see test_domain_package.py).
    monkeypatch.setenv("UNIVERSAL_AGENT_ALLOW_SCAFFOLD_STUB", "true")
