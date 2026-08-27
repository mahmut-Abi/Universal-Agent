from __future__ import annotations

from typing import TYPE_CHECKING

from universal_agent.core import DomainIdentity, JsonMapping, immutable_json
from universal_agent.runtime import RuntimeEventView
from universal_agent.security import (
    SecretResolution,
    SecretResolutionReport,
    redact_sensitive_mapping,
)
from universal_agent.service.views import (
    REDACTED_ENVIRONMENT_VALUE,
    RuntimeConfigDomainView,
    RuntimeConfigView,
    RuntimeModelConfigView,
    RuntimeSecretRefView,
)

if TYPE_CHECKING:
    from universal_agent.host.config import DomainConfig, ModelConfig, SecretRef


def secret_scan_payload(
    config: RuntimeConfigView,
    events: tuple[RuntimeEventView, ...],
) -> dict[str, object]:
    return {
        "config": {
            "environment": config.environment,
            "domains": [
                {
                    "name": domain.name,
                    "version": domain.version,
                    "settings": domain.settings,
                }
                for domain in config.domains
            ],
            "secrets": [
                {
                    "name": secret.name,
                    "source": secret.source,
                    "key": secret.key,
                    "required": secret.required,
                }
                for secret in config.secrets
            ],
        },
        "events": [dict(event.data) for event in events],
    }


def format_identities(identities: tuple[DomainIdentity, ...]) -> str:
    return ", ".join(f"{identity.name}@{identity.version}" for identity in identities) or "<none>"


def redact_environment(environment: JsonMapping) -> JsonMapping:
    return redact_sensitive_mapping(
        environment,
        replacement=REDACTED_ENVIRONMENT_VALUE,
    )


def runtime_config_domain_views(
    identities: tuple[DomainIdentity, ...],
    configs: tuple[DomainConfig, ...] = (),
) -> tuple[RuntimeConfigDomainView, ...]:
    config_by_identity = {
        DomainIdentity(config.name, config.version): config
        for config in configs
        if config.name is not None and config.version is not None
    }
    return tuple(
        RuntimeConfigDomainView(
            identity.name,
            identity.version,
            index == 0,
            backend=config.backend if (config := config_by_identity.get(identity)) else None,
            settings=(
                redact_environment(config.settings)
                if (config := config_by_identity.get(identity))
                else immutable_json()
            ),
        )
        for index, identity in enumerate(identities)
    )


def runtime_model_config_view(model: ModelConfig) -> RuntimeModelConfigView:
    return RuntimeModelConfigView(
        model.provider.value,
        model.name,
        model.endpoint,
        model.api_key_secret,
        model.timeout_seconds,
        redact_environment(model.headers),
        model.response_format,
    )


def runtime_secret_ref_views(
    secrets: tuple[SecretRef, ...],
    resolution: SecretResolutionReport | None = None,
) -> tuple[RuntimeSecretRefView, ...]:
    return tuple(_runtime_secret_ref_view(secret, resolution) for secret in secrets)


def secret_readiness_failure(report: SecretResolutionReport | None) -> str | None:
    if report is None or report.passed:
        return None
    return "missing required secrets: " + ", ".join(report.missing_required_names)


def not_ready_reason(
    *,
    has_domains: bool,
    has_capabilities: bool,
    has_tools: bool,
    missing_tools: tuple[str, ...],
) -> str:
    if not has_domains:
        return "no domains loaded"
    if not has_capabilities:
        return "no capabilities registered"
    if not has_tools:
        return "no tools registered"
    if missing_tools:
        return "capabilities without tools: " + ", ".join(missing_tools)
    return "not ready"


def _runtime_secret_ref_view(
    secret: SecretRef,
    resolution: SecretResolutionReport | None,
) -> RuntimeSecretRefView:
    resolved = _resolved_secret(resolution, secret.name)
    return RuntimeSecretRefView(
        secret.name,
        secret.source.value,
        secret.key,
        secret.required,
        available=None if resolved is None else resolved.available,
        status=None if resolved is None else resolved.status.value,
    )


def _resolved_secret(
    report: SecretResolutionReport | None,
    name: str,
) -> SecretResolution | None:
    if report is None:
        return None
    return report.get(name)
