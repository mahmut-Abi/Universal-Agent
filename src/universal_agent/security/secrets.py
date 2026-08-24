from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from os import environ
from typing import Protocol

REDACTED_SECRET_VALUES = frozenset({"", "<redacted>", "[REDACTED]", "***"})
PUBLIC_TOKEN_KEYS = frozenset(
    {
        "cached_tokens",
        "input_tokens",
        "model_input_tokens",
        "model_output_tokens",
        "model_total_tokens",
        "output_tokens",
        "total_tokens",
    }
)
SECRET_REFERENCE_CONTAINER_KEYS = frozenset(
    {
        "secret_ref",
        "secret_refs",
        "secret_reference",
        "secret_references",
        "secrets",
    }
)
SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)


@dataclass(frozen=True, slots=True)
class SecretFinding:
    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class SecretScanReport:
    findings: tuple[SecretFinding, ...]

    @property
    def passed(self) -> bool:
        return not self.findings

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(finding.path for finding in self.findings)


def scan_for_secrets(value: object, *, path: str = "$") -> SecretScanReport:
    return SecretScanReport(tuple(_scan_value(value, path)))


class SecretResolutionStatus(StrEnum):
    AVAILABLE = "available"
    MISSING_REQUIRED = "missing_required"
    MISSING_OPTIONAL = "missing_optional"
    UNSUPPORTED_SOURCE = "unsupported_source"


class SecretReference(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def source(self) -> object: ...

    @property
    def key(self) -> str: ...

    @property
    def required(self) -> bool: ...


class SecretProvider(Protocol):
    def get_secret(self, key: str) -> str | None:
        """Return a secret value for internal use only.

        Callers must not expose the returned value in runtime projections,
        events, logs, traces or diagnostics.
        """


@dataclass(frozen=True, slots=True)
class EnvSecretProvider:
    values: Mapping[str, str] | None = None

    def get_secret(self, key: str) -> str | None:
        source = environ if self.values is None else self.values
        value = source.get(key)
        if value is None or not value.strip():
            return None
        return value


@dataclass(frozen=True, slots=True)
class SecretResolution:
    name: str
    source: str
    key: str
    required: bool
    status: SecretResolutionStatus

    @property
    def available(self) -> bool:
        return self.status is SecretResolutionStatus.AVAILABLE

    @property
    def blocking(self) -> bool:
        return self.required and self.status is not SecretResolutionStatus.AVAILABLE


@dataclass(frozen=True, slots=True)
class SecretResolutionReport:
    items: tuple[SecretResolution, ...]

    @property
    def passed(self) -> bool:
        return not self.blocking

    @property
    def blocking(self) -> tuple[SecretResolution, ...]:
        return tuple(item for item in self.items if item.blocking)

    @property
    def missing_required_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.blocking)

    def get(self, name: str) -> SecretResolution | None:
        for item in self.items:
            if item.name == name:
                return item
        return None


def resolve_secret_refs(
    secrets: Sequence[SecretReference],
    *,
    provider: SecretProvider | None = None,
) -> SecretResolutionReport:
    active_provider = provider or EnvSecretProvider()
    return SecretResolutionReport(
        tuple(_resolve_secret_ref(secret, active_provider) for secret in secrets)
    )


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in PUBLIC_TOKEN_KEYS or normalized in SECRET_REFERENCE_CONTAINER_KEYS:
        return False
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _resolve_secret_ref(
    secret: SecretReference,
    provider: SecretProvider,
) -> SecretResolution:
    source = _source_name(secret.source)
    if source != "env":
        status = SecretResolutionStatus.UNSUPPORTED_SOURCE
    elif provider.get_secret(secret.key) is not None:
        status = SecretResolutionStatus.AVAILABLE
    elif secret.required:
        status = SecretResolutionStatus.MISSING_REQUIRED
    else:
        status = SecretResolutionStatus.MISSING_OPTIONAL
    return SecretResolution(
        name=secret.name,
        source=source,
        key=secret.key,
        required=secret.required,
        status=status,
    )


def _source_name(source: object) -> str:
    value = getattr(source, "value", source)
    return str(value)


def _scan_value(value: object, path: str) -> tuple[SecretFinding, ...]:
    if isinstance(value, Mapping):
        findings: list[SecretFinding] = []
        for raw_key, item in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}" if path else key
            if is_sensitive_key(key) and _contains_unredacted_scalar(item):
                findings.append(
                    SecretFinding(child_path, "sensitive key contains unredacted value")
                )
                continue
            findings.extend(_scan_value(item, child_path))
        return tuple(findings)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        findings = []
        for index, item in enumerate(value):
            findings.extend(_scan_value(item, f"{path}[{index}]"))
        return tuple(findings)
    return ()


def _contains_unredacted_scalar(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() not in REDACTED_SECRET_VALUES
    if isinstance(value, bool | int | float):
        return True
    if isinstance(value, Mapping):
        return any(_contains_unredacted_scalar(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return any(_contains_unredacted_scalar(item) for item in value)
    return bool(str(value).strip())
