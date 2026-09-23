"""CLI/runtime assembly for the sqldb domain."""

from __future__ import annotations

from pathlib import Path

from universal_agent.domains.sqldb import SqldbDomain
from universal_agent.host import RuntimeHost, build_configured_model_adapter
from universal_agent.profile import ProfileConfig
from universal_agent.security import EnvSecretProvider
from universal_agent.service import RuntimeService

_SQSQLDB_VERSION = "0.1.0"


def profile_domain_config(
    *,
    domain_backend: str,
    sqldb_path: str | None,
    timeout_seconds: float = 10.0,
) -> dict[str, object]:
    domain: dict[str, object] = {"name": "sqldb", "version": _SQSQLDB_VERSION}
    if domain_backend == "sqlite":
        if sqldb_path is None or not sqldb_path.strip():
            raise ValueError("sqlite backend requires --sqldb-path")
        domain["backend"] = "sqlite"
        domain["settings"] = {
            "path": sqldb_path.strip(),
            "timeout_seconds": timeout_seconds,
        }
        return domain
    raise ValueError(f"unsupported sqldb domain backend: {domain_backend}")


def build_sqldb_profile_service(profile_config_path: str | Path) -> RuntimeService:
    profile_config = ProfileConfig.from_json_file(profile_config_path)
    profile = profile_config.to_profile()
    secret_provider = EnvSecretProvider()
    domain = build_sqldb_domain(profile_config, secret_provider=secret_provider)
    host = RuntimeHost.from_profile(
        profile=profile,
        model=build_configured_model_adapter(profile.runtime, secret_provider=secret_provider),
        domain=domain,
        secret_provider=secret_provider,
    )
    return host.service


def build_sqldb_domain(
    profile_config: ProfileConfig, *, secret_provider: EnvSecretProvider | None = None
) -> SqldbDomain:
    from universal_agent.domains.sqldb.backend import SqliteSqlBackend as _Backend

    configured = profile_config.runtime.configured_domains()
    domain_config = next(
        (config for config in configured if config.name == "sqldb"),
        profile_config.domain,
    )
    raw = domain_config.settings if domain_config else {}
    path = raw.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError(
            "sqldb domain settings require a non-empty 'path' (the SQLite database file)"
        )
    timeout = raw.get("timeout_seconds")
    timeout_seconds = float(timeout) if isinstance(timeout, (int, float)) and timeout > 0 else 10.0
    return SqldbDomain(_Backend(path.strip(), timeout_seconds=timeout_seconds))
