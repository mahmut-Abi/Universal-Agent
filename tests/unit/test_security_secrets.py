from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent import SecretRef
from universal_agent.security import (
    EnvSecretProvider,
    FileSecretProvider,
    SecretResolutionError,
    SecretResolutionStatus,
    is_sensitive_key,
    redact_sensitive_mapping,
    redact_sensitive_value,
    resolve_secret_arguments,
    resolve_secret_refs,
    resolve_secret_value,
    scan_for_secrets,
)


class _DebugValue:
    def __str__(self) -> str:
        return "debug-value"


def test_secret_scanner_reports_unredacted_sensitive_values() -> None:
    report = scan_for_secrets(
        {
            "environment": "production",
            "api_token": "secret-token",
            "nested": {"password": "pw-value"},
        }
    )

    assert report.passed is False
    assert report.paths == ("$.api_token", "$.nested.password")


def test_secret_scanner_allows_redacted_values_and_secret_reference_metadata() -> None:
    report = scan_for_secrets(
        {
            "api_token": "<redacted>",
            "secrets": [
                {
                    "name": "openai_api_key",
                    "source": "env",
                    "key": "OPENAI_API_KEY",
                    "required": True,
                }
            ],
            "model_total_tokens": 42,
        }
    )

    assert report.passed is True
    assert is_sensitive_key("api_token") is True
    assert is_sensitive_key("secrets") is False
    assert is_sensitive_key("model_total_tokens") is False


def test_redact_sensitive_mapping_uses_shared_sensitive_key_rules() -> None:
    redacted = redact_sensitive_mapping(
        {
            "environment": "production",
            "access_key": "access-secret",
            "headers": {"authorization": "Bearer secret", "accept": "json"},
            "usage": {"model_total_tokens": 42},
            "items": [{"password": "pw-value", "name": "kept"}],
            "metadata": _DebugValue(),
        },
        replacement="<redacted>",
    )

    assert redacted == {
        "environment": "production",
        "access_key": "<redacted>",
        "headers": {"authorization": "<redacted>", "accept": "json"},
        "usage": {"model_total_tokens": 42},
        "items": [{"password": "<redacted>", "name": "kept"}],
        "metadata": "debug-value",
    }
    assert redact_sensitive_value("token", ["secret"], replacement="<redacted>") == "<redacted>"


def test_secret_resolver_reports_env_secret_availability_without_values() -> None:
    report = resolve_secret_refs(
        (
            SecretRef.env("openai_api_key", "OPENAI_API_KEY"),
            SecretRef.env("optional_token", "OPTIONAL_TOKEN", required=False),
        ),
        provider=EnvSecretProvider({"OPENAI_API_KEY": "secret-value"}),
    )

    required = report.get("openai_api_key")
    optional = report.get("optional_token")

    assert report.passed is True
    assert required is not None
    assert required.available is True
    assert required.status is SecretResolutionStatus.AVAILABLE
    assert optional is not None
    assert optional.available is False
    assert optional.status is SecretResolutionStatus.MISSING_OPTIONAL


def test_secret_resolver_blocks_missing_required_env_secrets() -> None:
    report = resolve_secret_refs(
        (SecretRef.env("openai_api_key", "OPENAI_API_KEY"),),
        provider=EnvSecretProvider({}),
    )

    resolved = report.get("openai_api_key")

    assert report.passed is False
    assert report.missing_required_names == ("openai_api_key",)
    assert resolved is not None
    assert resolved.status is SecretResolutionStatus.MISSING_REQUIRED


def test_secret_resolver_reads_file_secrets_without_projecting_values(tmp_path: Path) -> None:
    secret_path = tmp_path / "model-api-key"
    secret_path.write_text("file-secret-value\n", encoding="utf-8")
    report = resolve_secret_refs(
        (SecretRef.file("model_api_key", str(secret_path)),),
        provider=EnvSecretProvider({}),
    )

    resolved = report.get("model_api_key")

    assert report.passed is True
    assert resolved is not None
    assert resolved.source == "file"
    assert resolved.key == str(secret_path)
    assert resolved.status is SecretResolutionStatus.AVAILABLE
    assert resolve_secret_value(resolved, provider=EnvSecretProvider({})) == "file-secret-value"
    assert "file-secret-value" not in str(report)


def test_file_secret_provider_returns_none_for_missing_empty_or_escaped_paths(
    tmp_path: Path,
) -> None:
    empty_path = tmp_path / "empty-secret"
    empty_path.write_text("\n", encoding="utf-8")
    outside_path = tmp_path.parent / "outside-secret"
    outside_path.write_text("outside", encoding="utf-8")
    provider = FileSecretProvider(root=tmp_path)

    assert provider.get_secret(str(tmp_path / "missing-secret")) is None
    assert provider.get_secret(" ") is None
    assert provider.get_secret("empty-secret") is None
    assert provider.get_secret(str(outside_path)) is None


def test_secret_argument_resolver_replaces_declared_secret_refs() -> None:
    provider = EnvSecretProvider({"API_KEY": "secret-value"})
    report = resolve_secret_refs(
        (SecretRef.env("api_key", "API_KEY"),),
        provider=provider,
    )

    resolved = resolve_secret_arguments(
        {
            "headers": {"authorization": {"secret_ref": "api_key"}},
            "safe": "visible",
        },
        provider=provider,
        resolution=report,
    )

    assert resolved == {
        "headers": {"authorization": "secret-value"},
        "safe": "visible",
    }


def test_secret_argument_resolver_replaces_declared_file_secret_refs(tmp_path: Path) -> None:
    secret_path = tmp_path / "api-key"
    secret_path.write_text("file-secret-value\n", encoding="utf-8")
    report = resolve_secret_refs(
        (SecretRef.file("api_key", str(secret_path)),),
        provider=EnvSecretProvider({}),
    )

    resolved = resolve_secret_arguments(
        {"headers": {"authorization": {"secret_ref": "api_key"}}},
        provider=EnvSecretProvider({}),
        resolution=report,
    )

    assert resolved == {"headers": {"authorization": "file-secret-value"}}


def test_secret_argument_resolver_rejects_unknown_or_malformed_refs() -> None:
    provider = EnvSecretProvider({"API_KEY": "secret-value"})
    report = resolve_secret_refs(
        (SecretRef.env("api_key", "API_KEY"),),
        provider=provider,
    )

    with pytest.raises(SecretResolutionError, match="unknown runtime secret"):
        resolve_secret_arguments(
            {"token": {"secret_ref": "missing"}},
            provider=provider,
            resolution=report,
        )

    with pytest.raises(SecretResolutionError, match="must not include extra fields"):
        resolve_secret_arguments(
            {"token": {"secret_ref": "api_key", "label": "extra"}},
            provider=provider,
            resolution=report,
        )

    with pytest.raises(SecretResolutionError, match="secret reference name"):
        resolve_secret_arguments(
            {"token": {"secret_ref": " "}},
            provider=provider,
            resolution=report,
        )
