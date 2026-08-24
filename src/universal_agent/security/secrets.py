from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

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


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in PUBLIC_TOKEN_KEYS or normalized in SECRET_REFERENCE_CONTAINER_KEYS:
        return False
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


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
