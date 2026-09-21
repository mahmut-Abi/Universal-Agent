from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TextIO

from universal_agent.core import (
    DomainIdentity,
    JsonCodecError,
    JsonValue,
    SuccessCriterion,
    loads_json,
    parse_iso_datetime,
    to_json_value,
    write_json,
)
from universal_agent.core.config_validation import parse_json_value
from universal_agent.security import redact_sensitive_value


class CliExit(Exception):
    def __init__(self, status: int) -> None:
        self.status = status


def _parse_key_value_options(values: Sequence[str], label: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        key, separator, option_value = value.partition("=")
        if not separator or not key.strip() or not option_value.strip():
            raise ValueError(f"{label} must be KEY=VALUE")
        if key in parsed:
            raise ValueError(f"duplicate {label}: {key}")
        parsed[key] = option_value
    return parsed


def _success_criteria(values: Sequence[str]) -> tuple[SuccessCriterion, ...]:
    if not values:
        return (SuccessCriterion("healthy", True),)
    parsed: dict[str, JsonValue] = {}
    for value in values:
        key, separator, raw_expected = value.partition("=")
        if not separator or not key.strip() or not raw_expected.strip():
            raise ValueError("success criterion must be KEY=JSON")
        key = key.strip()
        if key in parsed:
            raise ValueError(f"duplicate success criterion: {key}")
        parsed[key] = _parse_success_json_value(raw_expected, key)
    return tuple(SuccessCriterion(key, expected) for key, expected in parsed.items())


def _parse_success_json_value(value: str, key: str) -> JsonValue:
    try:
        loaded = loads_json(value)
    except JsonCodecError:
        # Lenient fallback (UA-LIVE-2026-09-21 F8): bare words, numbers and
        # booleans are accepted as JSON string/number/bool literals so
        # `--success root_cause=image_pull_back_off` works without shell
        # quoting. Values that are only valid as strings fail validation
        # downstream; explicitly quoted JSON still wins.
        loaded = value
    return parse_json_value(loaded, f"success.{key}")


def _parse_domain_identity(value: str) -> DomainIdentity:
    if "@" not in value:
        raise ValueError(f"domain package dependency must be name@version: {value}")
    name, version = value.split("@", 1)
    if not name.strip() or not version.strip():
        raise ValueError(f"domain package dependency must be name@version: {value}")
    return DomainIdentity(name, version)


def _write_json(out: TextIO, payload: object) -> None:
    write_json(out, to_json_value(payload, fallback_to_string=True), indent=True)


def _write_text(out: TextIO, payload: str) -> None:
    out.write(payload)


def _write_error(out: TextIO, code: str, message: str) -> None:
    safe_message = str(redact_sensitive_value("error", message, replacement="<redacted>"))
    hint = _repair_hint(code, safe_message)
    _write_json(
        out,
        {
            "error": {
                "code": code,
                "message": safe_message,
                "reason": safe_message,
                "try": hint,
                "text": f"Error: {code}\nReason: {safe_message}\nTry: {hint}",
            }
        },
    )


def _domain_error_keywords() -> frozenset[str]:
    """Domain names from contributions, for error-hint keyword matching."""

    from universal_agent_cli.contributions import load_cli_contributions

    return frozenset(contribution.domain for contribution in load_cli_contributions())


def _repair_hint(code: str, message: str) -> str:
    lower = f"{code} {message}".lower()
    if "profile config not found" in lower:
        return "Run `agent init` or pass the same --profile-config used for setup."
    if "unknown profile" in lower or "profile not found" in lower:
        return "Run `agent profile list` and retry with one of the listed profiles."
    if "api key" in lower or "credential" in lower or "secret" in lower:
        return "Set the required environment variable or re-run `agent init` with a scripted model."
    if "agentd" in lower or "api-url" in lower or "connection" in lower:
        return "Start agentd, check --api-url/--api-token, or omit --api-url for embedded mode."
    if "policy" in lower or "confirmed" in lower or "confirmation" in lower:
        return "Review the pending action and retry with the explicit confirmation flag."
    if (
        "domain" in lower
        or "backend" in lower
        or any(keyword in lower for keyword in _domain_error_keywords())
    ):
        return "Run `agent doctor` and verify the selected profile/domain backend configuration."
    if "tool" in lower:
        return "Run `agent doctor`, then inspect `agent session events <id>` for tool details."
    if "session not found" in lower:
        return "Run `agent session list` and retry with an existing session id."
    return "Run `agent doctor`; for details retry with --output json where supported."


def _doctor_should_fail(status: str, fail_on: str) -> bool:
    if fail_on == "never":
        return False
    if status == "error":
        return fail_on in {"error", "warn"}
    if status == "warn":
        return fail_on == "warn"
    return False


def _optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value == "true"


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return parse_iso_datetime(
        value,
        field="--before",
        description="an ISO 8601 datetime",
        require_timezone=True,
    )


_MUTATION_GOAL_HINTS = (
    "scale ",
    "restart ",
    "set the container image",
    "change the container image",
    "update the image",
    "set the image",
    "fix it by setting",
)


def _warn_mutation_goal_without_criteria(
    goal: str,
    success_flags: list[str],
) -> None:
    """UA-LIVE-2026-09-21 P10b: a mutation-shaped goal without explicit
    success criteria can complete on the default pre-mutation ``healthy``
    criterion without ever performing the mutation. Warn the operator."""

    import sys

    if success_flags:
        return
    lowered = goal.lower()
    if not any(hint in lowered for hint in _MUTATION_GOAL_HINTS):
        return
    sys.stderr.write(
        "Warning: this goal looks like a mutation but no --success criteria were "
        "given; the default 'healthy' criterion can be satisfied by the "
        "pre-mutation state. Pass e.g. --success replicas=3 to verify the "
        "mutation actually happened.\n"
    )
