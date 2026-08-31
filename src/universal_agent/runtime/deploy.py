from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = 8080


class DeploymentMode(StrEnum):
    EMBEDDED = "embedded"
    LOCAL = "local"
    SERVER = "server"


class InvalidDeploymentMode(ValueError):
    pass


_MODE_ALIASES: dict[str, DeploymentMode] = {
    "embedded": DeploymentMode.EMBEDDED,
    "inprocess": DeploymentMode.EMBEDDED,
    "in_process": DeploymentMode.EMBEDDED,
    "local": DeploymentMode.LOCAL,
    "local_service": DeploymentMode.LOCAL,
    "server": DeploymentMode.SERVER,
    "remote": DeploymentMode.SERVER,
    "remote_service": DeploymentMode.SERVER,
}


@dataclass(frozen=True, slots=True)
class DeploymentConfig:
    mode: DeploymentMode
    enable_agentd: bool
    enable_persistence: bool
    bind_host: str | None = None
    bind_port: int | None = None

    def validate(self) -> None:
        if self.mode is DeploymentMode.SERVER:
            if not self.bind_host or not self.bind_host.strip():
                raise ValueError("server deployment requires bind_host")
            if self.bind_port is None:
                raise ValueError("server deployment requires bind_port")
            if self.bind_port <= 0 or self.bind_port > 65535:
                raise ValueError(f"bind_port must be between 1 and 65535: {self.bind_port}")
        elif self.bind_host is not None or self.bind_port is not None:
            raise ValueError(f"{self.mode.value} deployment must not set bind_host/bind_port")


def resolve_deployment_mode(value: str) -> DeploymentMode:
    normalized = value.strip().lower()
    mode = _MODE_ALIASES.get(normalized)
    if mode is None:
        accepted = ", ".join(sorted({item.value for item in DeploymentMode}))
        raise InvalidDeploymentMode(
            f"unsupported deployment mode: {value!r}; expected one of {accepted}"
        )
    return mode


def deployment_from_config(
    *,
    mode: str | None,
    enable_agentd: bool = False,
    enable_persistence: bool = False,
    bind_host: str | None = None,
    bind_port: int | None = None,
) -> DeploymentConfig:
    resolved_mode = resolve_deployment_mode(mode) if mode else DeploymentMode.EMBEDDED

    if resolved_mode is DeploymentMode.SERVER:
        agentd = True
        host = bind_host or DEFAULT_BIND_HOST
        port = bind_port if bind_port is not None else DEFAULT_BIND_PORT
        return DeploymentConfig(
            mode=resolved_mode,
            enable_agentd=agentd,
            enable_persistence=enable_persistence,
            bind_host=host,
            bind_port=port,
        )

    if resolved_mode is DeploymentMode.LOCAL:
        return DeploymentConfig(
            mode=resolved_mode,
            enable_agentd=enable_agentd,
            enable_persistence=enable_persistence,
            bind_host=None,
            bind_port=None,
        )

    return DeploymentConfig(
        mode=resolved_mode,
        enable_agentd=False,
        enable_persistence=enable_persistence,
        bind_host=None,
        bind_port=None,
    )
