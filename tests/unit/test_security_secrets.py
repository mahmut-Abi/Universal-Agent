from __future__ import annotations

from universal_agent import SecretRef
from universal_agent.security import (
    EnvSecretProvider,
    SecretResolutionStatus,
    is_sensitive_key,
    resolve_secret_refs,
    scan_for_secrets,
)


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
