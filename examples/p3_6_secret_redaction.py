from __future__ import annotations

from universal_agent import redact_sensitive_mapping, redact_sensitive_value


def main() -> None:
    payload = {
        "environment": "production",
        "api_token": "live-token",
        "headers": {"authorization": "Bearer live-token", "accept": "json"},
        "usage": {"model_total_tokens": 42},
        "items": [{"password": "pw-value", "name": "kept"}],
    }

    config_projection = redact_sensitive_mapping(payload, replacement="<redacted>")
    log_projection = redact_sensitive_mapping(payload)
    log_headers = log_projection["headers"]
    log_usage = log_projection["usage"]
    assert isinstance(log_headers, dict)
    assert isinstance(log_usage, dict)

    print(f"config_api_token={config_projection['api_token']}")
    print(f"log_authorization={log_headers['authorization']}")
    print(f"usage_tokens={log_usage['model_total_tokens']}")
    print(f"token_list={redact_sensitive_value('token', ['secret'])}")


if __name__ == "__main__":
    main()
