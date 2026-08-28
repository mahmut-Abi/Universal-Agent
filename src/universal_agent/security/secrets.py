from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from os import environ
from pathlib import Path
from typing import Protocol

from universal_agent.core import JsonMapping, JsonValue, immutable_json
from universal_agent.core.config_validation import parse_non_empty_string

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
    "access_key",
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


def redact_sensitive_mapping(
    values: Mapping[str, object],
    *,
    replacement: str = "[REDACTED]",
) -> JsonMapping:
    """Return a JSON-safe mapping with sensitive keyed values replaced.

    This is the shared security projection seam for logs, traces, config views,
    diagnostics and future HTTP surfaces. The caller chooses the replacement
    token, but the sensitive-key vocabulary stays runtime-owned here.
    """

    return immutable_json(
        {
            str(key): redact_sensitive_value(str(key), value, replacement=replacement)
            for key, value in values.items()
        }
    )


def redact_sensitive_value(
    key: str,
    value: object,
    *,
    replacement: str = "[REDACTED]",
) -> JsonValue:
    if is_sensitive_key(key):
        return replacement
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        return {
            str(item_key): redact_sensitive_value(
                str(item_key),
                item,
                replacement=replacement,
            )
            for item_key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [redact_sensitive_value(key, item, replacement=replacement) for item in value]
    return str(value)


class SecretResolutionError(ValueError):
    pass


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
class FileSecretProvider:
    root: str | Path | None = None

    def get_secret(self, key: str) -> str | None:
        path = _file_secret_path(key, self.root)
        if path is None or not path.is_file():
            return None
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not value:
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


def resolve_secret_value(
    secret: SecretReference,
    *,
    provider: SecretProvider | None = None,
) -> str | None:
    source = _source_name(secret.source)
    if source == "env":
        return (provider or EnvSecretProvider()).get_secret(secret.key)
    if source == "file":
        return FileSecretProvider().get_secret(secret.key)
    return None


def resolve_secret_arguments(
    arguments: JsonMapping,
    *,
    provider: SecretProvider,
    resolution: SecretResolutionReport | None,
) -> JsonMapping:
    return immutable_json(
        {
            key: _resolve_secret_argument_value(
                value,
                provider=provider,
                resolution=resolution,
                path=f"$.{key}",
            )
            for key, value in arguments.items()
        }
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
    if source not in {"env", "file"}:
        status = SecretResolutionStatus.UNSUPPORTED_SOURCE
    elif resolve_secret_value(secret, provider=provider) is not None:
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


def _resolve_secret_argument_value(
    value: JsonValue,
    *,
    provider: SecretProvider,
    resolution: SecretResolutionReport | None,
    path: str,
) -> JsonValue:
    if isinstance(value, Mapping):
        secret_name = _secret_reference_name(value, path)
        if secret_name is not None:
            return _resolve_declared_secret(
                secret_name,
                provider=provider,
                resolution=resolution,
                path=path,
            )
        return {
            str(key): _resolve_secret_argument_value(
                item,
                provider=provider,
                resolution=resolution,
                path=f"{path}.{key}",
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_secret_argument_value(
                item,
                provider=provider,
                resolution=resolution,
                path=f"{path}[{index}]",
            )
            for index, item in enumerate(value)
        ]
    return value


def _secret_reference_name(value: Mapping[str, JsonValue], path: str) -> str | None:
    if "secret_ref" not in value and "secret_reference" not in value:
        return None
    if len(value) != 1:
        raise SecretResolutionError(f"{path} secret reference must not include extra fields")
    raw_name = value.get("secret_ref", value.get("secret_reference"))
    try:
        return parse_non_empty_string(raw_name, f"{path} secret reference name")
    except ValueError as exc:
        raise SecretResolutionError(
            f"{path} secret reference name must be a non-empty string"
        ) from exc


def _resolve_declared_secret(
    name: str,
    *,
    provider: SecretProvider,
    resolution: SecretResolutionReport | None,
    path: str,
) -> str:
    if resolution is None:
        raise SecretResolutionError(f"{path} secret reference has no runtime secret registry")
    declared = resolution.get(name)
    if declared is None:
        raise SecretResolutionError(f"{path} references unknown runtime secret: {name}")
    if declared.status is SecretResolutionStatus.UNSUPPORTED_SOURCE:
        raise SecretResolutionError(
            f"{path} references unsupported secret source: {declared.source}"
        )
    value = resolve_secret_value(declared, provider=provider)
    if value is None:
        raise SecretResolutionError(f"{path} references unavailable runtime secret: {name}")
    return value


def _file_secret_path(key: str, root: str | Path | None) -> Path | None:
    try:
        key = parse_non_empty_string(key, "file secret key").strip()
    except ValueError:
        return None
    raw_path = Path(key).expanduser()
    if root is None:
        return raw_path.resolve()
    root_path = Path(root).expanduser().resolve()
    path = raw_path if raw_path.is_absolute() else root_path / raw_path
    resolved = path.resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError:
        return None
    return resolved


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
