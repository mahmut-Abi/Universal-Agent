from __future__ import annotations

import pytest

from universal_agent.runtime.deploy import (
    DeploymentConfig,
    DeploymentMode,
    InvalidDeploymentMode,
    deployment_from_config,
    resolve_deployment_mode,
)


def test_resolve_known_modes() -> None:
    assert resolve_deployment_mode("embedded") is DeploymentMode.EMBEDDED
    assert resolve_deployment_mode("local") is DeploymentMode.LOCAL
    assert resolve_deployment_mode("server") is DeploymentMode.SERVER


def test_resolve_aliases() -> None:
    assert resolve_deployment_mode("inprocess") is DeploymentMode.EMBEDDED
    assert resolve_deployment_mode("in_process") is DeploymentMode.EMBEDDED
    assert resolve_deployment_mode("local_service") is DeploymentMode.LOCAL
    assert resolve_deployment_mode("remote") is DeploymentMode.SERVER
    assert resolve_deployment_mode("remote_service") is DeploymentMode.SERVER


def test_resolve_case_insensitive_and_whitespace() -> None:
    assert resolve_deployment_mode("  SERVER ") is DeploymentMode.SERVER
    assert resolve_deployment_mode("Embedded") is DeploymentMode.EMBEDDED


def test_resolve_invalid_raises() -> None:
    with pytest.raises(InvalidDeploymentMode):
        resolve_deployment_mode("cluster")
    with pytest.raises(InvalidDeploymentMode):
        resolve_deployment_mode("")


def test_embedded_defaults() -> None:
    config = deployment_from_config(mode="embedded")
    assert config.mode is DeploymentMode.EMBEDDED
    assert config.enable_agentd is False
    assert config.bind_host is None
    assert config.bind_port is None


def test_local_defaults() -> None:
    config = deployment_from_config(mode="local")
    assert config.mode is DeploymentMode.LOCAL
    assert config.enable_agentd is False
    assert config.bind_host is None
    assert config.bind_port is None


def test_local_explicit_agentd() -> None:
    config = deployment_from_config(mode="local", enable_agentd=True, enable_persistence=True)
    assert config.enable_agentd is True
    assert config.enable_persistence is True
    assert config.bind_host is None
    assert config.bind_port is None


def test_server_defaults_bind() -> None:
    config = deployment_from_config(mode="server")
    assert config.mode is DeploymentMode.SERVER
    assert config.enable_agentd is True
    assert config.bind_host == "127.0.0.1"
    assert config.bind_port == 8080


def test_server_explicit_bind() -> None:
    config = deployment_from_config(mode="server", bind_host="0.0.0.0", bind_port=9090)
    assert config.enable_agentd is True
    assert config.bind_host == "0.0.0.0"
    assert config.bind_port == 9090


def test_server_ignores_agentd_default_override() -> None:
    config = deployment_from_config(mode="server", enable_agentd=False)
    assert config.enable_agentd is True


def test_none_mode_defaults_embedded() -> None:
    config = deployment_from_config(mode=None)
    assert config.mode is DeploymentMode.EMBEDDED
    assert config.enable_agentd is False


def test_server_validate_ok() -> None:
    DeploymentConfig(
        mode=DeploymentMode.SERVER,
        enable_agentd=True,
        enable_persistence=False,
        bind_host="0.0.0.0",
        bind_port=8080,
    ).validate()


def test_server_validate_missing_host() -> None:
    with pytest.raises(ValueError):
        DeploymentConfig(
            mode=DeploymentMode.SERVER,
            enable_agentd=True,
            enable_persistence=False,
            bind_host=None,
            bind_port=8080,
        ).validate()


def test_server_validate_missing_port() -> None:
    with pytest.raises(ValueError):
        DeploymentConfig(
            mode=DeploymentMode.SERVER,
            enable_agentd=True,
            enable_persistence=False,
            bind_host="0.0.0.0",
            bind_port=None,
        ).validate()


def test_server_validate_bad_port_range() -> None:
    with pytest.raises(ValueError):
        DeploymentConfig(
            mode=DeploymentMode.SERVER,
            enable_agentd=True,
            enable_persistence=False,
            bind_host="0.0.0.0",
            bind_port=70000,
        ).validate()


def test_non_server_rejects_bind() -> None:
    with pytest.raises(ValueError):
        DeploymentConfig(
            mode=DeploymentMode.EMBEDDED,
            enable_agentd=False,
            enable_persistence=False,
            bind_host="0.0.0.0",
            bind_port=8080,
        ).validate()
