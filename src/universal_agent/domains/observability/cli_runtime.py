"""CLI/runtime assembly for the observability domain.

Builds a RuntimeService from an `agent init` profile whose domain is
`observability` (backend `prometheus`, VictoriaMetrics-compatible), so the
domain is reachable through the CLI Golden Path (UA-LIVE-2026-09-21 R6-1).
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from universal_agent.configuration import DomainConfig
from universal_agent.core import JsonMapping
from universal_agent.domains.observability import ObservabilityDomain
from universal_agent.host import RuntimeHost
from universal_agent.profile import ProfileConfig
from universal_agent.security import (
    EnvSecretProvider,
    SecretProvider,
    resolve_secret_value,
)
from universal_agent.service import RuntimeService

_OBSERVABILITY_VERSION = "0.2.0"


def profile_domain_config(
    *,
    domain_backend: str,
    endpoint: str | None,
    bearer_token_secret: str | None,
    timeout_seconds: float,
) -> dict[str, object]:
    """Domain config contributed by `agent init` for the observability domain."""

    domain: dict[str, object] = {"name": "observability", "version": _OBSERVABILITY_VERSION}
    if domain_backend == "prometheus":
        if endpoint is None or not endpoint.strip():
            raise ValueError("prometheus backend requires --observability-endpoint")
        settings: dict[str, object] = {
            "base_url": endpoint.strip(),
            "timeout_seconds": timeout_seconds,
        }
        if bearer_token_secret is not None:
            settings["bearer_token_secret"] = bearer_token_secret
        domain["backend"] = "prometheus"
        domain["settings"] = settings
        return domain
    raise ValueError(f"unsupported observability domain backend: {domain_backend}")


def build_observability_profile_service(profile_config_path: str | Path) -> RuntimeService:
    """Build a RuntimeService from an `agent init` observability profile."""

    from universal_agent.host import build_configured_model_adapter as _build_model

    profile_config = ProfileConfig.from_json_file(profile_config_path)
    profile = profile_config.to_profile()
    secret_provider = EnvSecretProvider()
    domain = build_observability_domain(profile_config, secret_provider=secret_provider)
    host = RuntimeHost.from_profile(
        profile=profile,
        model=_build_model(profile.runtime, secret_provider=secret_provider),
        domain=domain,
        secret_provider=secret_provider,
    )
    return host.service


def build_observability_domain(
    profile_config: ProfileConfig,
    *,
    secret_provider: SecretProvider | None = None,
    domain_config: DomainConfig | None = None,
) -> ObservabilityDomain:
    """Build the ObservabilityDomain from a profile's domain settings.

    ``domain_config`` overrides the profile's primary domain — required for
    combined profiles where observability is not the primary domain.
    """

    from universal_agent.domains.observability.prometheus import PrometheusBackend

    resolved = domain_config
    if resolved is None:
        configured = profile_config.runtime.configured_domains()
        resolved = next(
            (config for config in configured if config.name == "observability"),
            profile_config.domain,
        )
    settings = _observability_settings(resolved.settings if resolved else {})
    runtime_config = profile_config.to_profile().runtime
    backend = PrometheusBackend(
        settings.base_url,
        headers=_observability_headers(
            settings, runtime_config, secret_provider or EnvSecretProvider()
        ),
        timeout_seconds=settings.timeout_seconds,
    )
    return ObservabilityDomain(backend)


class _ObservabilitySettings:
    """Validated view of the `observability` domain settings block."""

    __slots__ = ("base_url", "bearer_token_secret", "timeout_seconds")

    def __init__(
        self,
        *,
        base_url: str,
        bearer_token_secret: str | None,
        timeout_seconds: float,
    ) -> None:
        self.base_url = base_url
        self.bearer_token_secret = bearer_token_secret
        self.timeout_seconds = timeout_seconds


def _observability_settings(settings: JsonMapping | None) -> _ObservabilitySettings:
    raw = settings or {}
    endpoint = raw.get("base_url")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ValueError(
            "observability domain settings require a non-empty 'base_url' (the "
            "Prometheus/VictoriaMetrics query API base URL)"
        )
    return _ObservabilitySettings(
        base_url=endpoint.strip(),
        bearer_token_secret=_optional_setting(raw, "bearer_token_secret"),
        timeout_seconds=_timeout(raw),
    )


def _optional_setting(settings: JsonMapping, key: str) -> str | None:
    value = settings.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _timeout(settings: JsonMapping) -> float:
    value = settings.get("timeout_seconds")
    if value is None:
        return 15.0
    timeout = cast("float", value) if isinstance(value, (int, float)) else None
    if timeout is None or timeout <= 0:
        raise ValueError("observability timeout_seconds must be a positive number")
    return float(timeout)


def _observability_headers(
    settings: _ObservabilitySettings,
    runtime_config: object,
    secret_provider: SecretProvider,
) -> dict[str, str] | None:
    """Authorization headers from the profile's bearer token secret, if any."""

    if settings.bearer_token_secret is None:
        return None
    from universal_agent.configuration import RuntimeConfig

    if not isinstance(runtime_config, RuntimeConfig):
        raise ValueError("observability bearer_token_secret requires runtime config")
    for secret in runtime_config.secrets:
        if secret.name == settings.bearer_token_secret:
            token = resolve_secret_value(secret, provider=secret_provider)
            if token:
                return {"Authorization": f"Bearer {token}"}
            return None
    raise ValueError(
        f"observability bearer_token_secret is not declared in profile secrets: "
        f"{settings.bearer_token_secret}"
    )
