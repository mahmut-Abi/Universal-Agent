"""CLI/runtime assembly for the code domain."""

from __future__ import annotations

from pathlib import Path

from universal_agent.domains.code import CodeDomain
from universal_agent.domains.code.backend import ShellBackend
from universal_agent.host import RuntimeHost, build_configured_model_adapter
from universal_agent.profile import ProfileConfig
from universal_agent.security import EnvSecretProvider
from universal_agent.service import RuntimeService


def profile_domain_config(
    *,
    domain_backend: str,
    code_workspace: str | None,
    code_timeout_seconds: float = 30.0,
) -> dict[str, object]:
    domain: dict[str, object] = {"name": "code", "version": "0.1.0"}
    if domain_backend == "shell":
        if code_workspace is None or not code_workspace.strip():
            raise ValueError("shell backend requires --code-workspace")
        domain["backend"] = "shell"
        domain["settings"] = {
            "workspace_path": code_workspace.strip(),
            "timeout_seconds": code_timeout_seconds,
        }
        return domain
    raise ValueError(f"unsupported code domain backend: {domain_backend}")


def build_code_profile_service(profile_config_path: str | Path) -> RuntimeService:
    profile_config = ProfileConfig.from_json_file(profile_config_path)
    profile = profile_config.to_profile()
    secret_provider = EnvSecretProvider()
    domain = build_code_domain(profile_config, secret_provider=secret_provider)
    host = RuntimeHost.from_profile(
        profile=profile,
        model=build_configured_model_adapter(profile.runtime, secret_provider=secret_provider),
        domain=domain,
        secret_provider=secret_provider,
    )
    return host.service


def build_code_domain(
    profile_config: ProfileConfig,
    *,
    secret_provider: EnvSecretProvider | None = None,
) -> CodeDomain:
    configured = profile_config.runtime.configured_domains()
    domain_config = next(
        (config for config in configured if config.name == "code"),
        profile_config.domain,
    )
    raw = domain_config.settings if domain_config else {}
    workspace = raw.get("workspace_path")
    if not isinstance(workspace, str) or not workspace.strip():
        raise ValueError("code domain settings require a non-empty 'workspace_path'")
    timeout = raw.get("timeout_seconds")
    timeout_seconds = float(timeout) if isinstance(timeout, (int, float)) and timeout > 0 else 30.0
    return CodeDomain(ShellBackend(workspace.strip(), timeout_seconds=timeout_seconds))
