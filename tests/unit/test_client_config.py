"""Client/server separation: config-file server target resolution (UA-CS-1).

A client machine specifies the remote agentd address in its user config
(`server.url` + optional `server.auth_token_env`) or via AGENT_API_URL;
explicit --api-url always wins.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent_cli import run_cli
from universal_agent_cli.client_config import (
    load_client_server_target,
    resolve_client_server_target,
    resolve_client_token,
)

pytestmark = pytest.mark.unit


def _write_config(config_dir: Path, server: dict[str, object] | None) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "config.json"
    payload: dict[str, object] = {"environment": "local"}
    if server is not None:
        payload["server"] = server
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_target_reads_agent_config_dir(tmp_path: Path) -> None:
    config_dir = tmp_path / "universal-agent"
    _write_config(
        config_dir,
        {"url": "http://agentd.internal:8765", "auth_token_env": "AGENTD_AUTH_TOKEN"},
    )

    target = load_client_server_target({"AGENT_CONFIG_DIR": str(config_dir)})

    assert target is not None
    assert target.url == "http://agentd.internal:8765"
    assert target.auth_token_env == "AGENTD_AUTH_TOKEN"


def test_load_target_without_server_section_is_none(tmp_path: Path) -> None:
    config_dir = tmp_path / "universal-agent"
    _write_config(config_dir, None)

    assert load_client_server_target({"AGENT_CONFIG_DIR": str(config_dir)}) is None


def test_load_target_ignores_malformed_config(tmp_path: Path) -> None:
    config_dir = tmp_path / "universal-agent"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text("{not json", encoding="utf-8")

    assert load_client_server_target({"AGENT_CONFIG_DIR": str(config_dir)}) is None


def test_resolution_priority_flag_over_env_over_file(tmp_path: Path) -> None:
    config_dir = tmp_path / "universal-agent"
    _write_config(config_dir, {"url": "http://from-config:1"})
    environ = {
        "AGENT_CONFIG_DIR": str(config_dir),
        "AGENT_API_URL": "http://from-env:2",
    }

    flag = resolve_client_server_target("http://from-flag:3", environ)
    env = resolve_client_server_target(None, environ)
    file = resolve_client_server_target(None, {"AGENT_CONFIG_DIR": str(config_dir)})

    assert flag is not None and flag.url == "http://from-flag:3"
    assert env is not None and env.url == "http://from-env:2"
    assert file is not None and file.url == "http://from-config:1"


def test_token_resolution_from_config_env_name(tmp_path: Path) -> None:
    config_dir = tmp_path / "universal-agent"
    _write_config(
        config_dir,
        {"url": "http://agentd:1", "auth_token_env": "MY_TOKEN_ENV"},
    )
    target = load_client_server_target({"AGENT_CONFIG_DIR": str(config_dir)})

    token = resolve_client_token(
        target, {"AGENT_CONFIG_DIR": str(config_dir), "MY_TOKEN_ENV": "secret-value"}
    )

    assert token == "secret-value"


@pytest.mark.asyncio
async def test_init_writes_server_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`agent init --server-url ...` stores the thin-client target."""

    from io import StringIO

    output_dir = tmp_path / "universal-agent"
    status = await run_cli(
        [
            "init",
            "--output-format",
            "json",
            "--output",
            str(output_dir / "profile.json"),
            "--server-url",
            "http://agentd.internal:8765",
            "--server-auth-token-env",
            "AGENTD_AUTH_TOKEN",
            "--output",
            str(output_dir / "profile.json"),
            "--force",
        ],
        stdout=StringIO(),
    )
    assert status == 0

    config = json.loads((output_dir / "config.json").read_text(encoding="utf-8"))
    assert config["server"] == {
        "url": "http://agentd.internal:8765",
        "auth_token_env": "AGENTD_AUTH_TOKEN",
    }

    target = load_client_server_target({"AGENT_CONFIG_DIR": str(output_dir)})
    assert target is not None
    assert target.url == "http://agentd.internal:8765"


@pytest.mark.asyncio
async def test_run_with_configured_server_target_uses_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured server target switches `agent run` to thin-client mode:
    it must NOT spawn the embedded runtime; an unreachable server surfaces as
    a structured error instead of a local kernel run."""

    from io import StringIO

    config_dir = tmp_path / "universal-agent"
    _write_config(config_dir, {"url": "http://127.0.0.1:1"})

    monkeypatch.chdir(tmp_path)
    out = StringIO()
    err = StringIO()
    status = await run_cli(["run", "Hello"], stdout=out, stderr=err)

    assert status != 0
    text = err.getvalue()
    assert "agentd" in text or "unreachable" in text or "connection" in text.lower()
