from __future__ import annotations

from universal_agent.security import is_sensitive_key, scan_for_secrets


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
