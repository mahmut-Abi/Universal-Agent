"""`agent doctor` — the Golden Path preflight check.

The doctor answers two questions for a new user:

1. Is my environment/config/model/runtime wired correctly?
2. When something is wrong, what is the *next fix*?

The report is human text by default (``--output json`` for machines) and never
raises a stack trace at the user: every failed check carries a ``Try:`` hint.
"""

from __future__ import annotations

import argparse
import platform
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO, cast

from universal_agent.core import JsonValue
from universal_agent.profile import default_profile_config_path
from universal_agent_cli.config import validate_profile_config_file
from universal_agent_cli.io import _write_json, _write_text

MINIMUM_PYTHON = (3, 12)

_DEPENDENCY_PROBES = (
    "pydantic",
    "sqlalchemy",
    "httpx",
    "orjson",
    "openai",
)

CHECK_OK = "ok"
CHECK_ERROR = "error"
CHECK_SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    section: str
    name: str
    status: str  # CHECK_OK | CHECK_ERROR | CHECK_SKIPPED
    message: str
    hint: str | None = None

    @property
    def symbol(self) -> str:
        if self.status == CHECK_OK:
            return "✓"
        if self.status == CHECK_ERROR:
            return "✗"
        return "-"


def _check(
    section: str,
    name: str,
    passed: bool,
    message: str,
    hint: str | None = None,
) -> DoctorCheck:
    return DoctorCheck(section, name, CHECK_OK if passed else CHECK_ERROR, message, hint)


def _python_message() -> str:
    minimum = f"{MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]}"
    return f"Python {platform.python_version()} (requires >= {minimum})"


def _environment_checks() -> list[DoctorCheck]:
    checks = [
        _check(
            "Environment",
            "Python",
            sys.version_info >= MINIMUM_PYTHON,
            _python_message(),
            None
            if sys.version_info >= MINIMUM_PYTHON
            else "Install Python 3.12 or newer, then re-run `agent doctor`.",
        )
    ]
    missing: list[str] = []
    for module in _DEPENDENCY_PROBES:
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    checks.append(
        _check(
            "Environment",
            "dependencies",
            not missing,
            "all core dependencies importable" if not missing else f"missing: {', '.join(missing)}",
            None
            if not missing
            else "Run `uv sync` to install dependencies, then re-run `agent doctor`.",
        )
    )
    return checks


def _config_checks(args: argparse.Namespace) -> tuple[list[DoctorCheck], Path | None]:
    explicit = cast(str | None, args.profile_config)
    config_path = Path(explicit) if explicit is not None else default_profile_config_path()
    if not config_path.is_file():
        return (
            [
                _check(
                    "Configuration",
                    "config found",
                    False,
                    f"no profile config at {config_path}",
                    "Run `agent init` to create one, or pass --profile-config <path>.",
                )
            ],
            None,
        )
    return (
        [_check("Configuration", "config found", True, str(config_path))],
        config_path,
    )


def _config_validity_check(
    config_path: Path,
) -> tuple[DoctorCheck, Mapping[str, JsonValue] | None]:
    try:
        report = validate_profile_config_file(config_path)
    except Exception as exc:
        return (
            _check(
                "Configuration",
                "config valid",
                False,
                f"{config_path}: {exc}",
                "Run `agent config validate --profile-config "
                + str(config_path)
                + "` for details, or re-run `agent init --force` to reset it.",
            ),
            None,
        )
    profile = report.get("profile")
    profile_name = ""
    if isinstance(profile, Mapping):
        profile_name = str(profile.get("name", ""))
    return (
        _check(
            "Configuration",
            "config valid",
            True,
            f"profile: {profile_name}" if profile_name else "valid",
        ),
        report,
    )


def _model_checks(report: Mapping[str, JsonValue] | None) -> list[DoctorCheck]:
    if report is None:
        return []
    runtime = report.get("runtime")
    model = runtime.get("model") if isinstance(runtime, Mapping) else None
    if not isinstance(model, Mapping):
        return []
    provider = str(model.get("provider", ""))
    name = str(model.get("name", ""))
    checks = [
        _check("Model", "provider configured", bool(provider), provider or "<missing>"),
        _check("Model", "model configured", bool(name), name or "<missing>"),
    ]
    if provider == "scripted":
        checks.append(
            _check(
                "Model",
                "credentials configured",
                True,
                "offline scripted model needs no credentials",
                "For a real model, re-run `agent init --force --model-provider "
                "openai_chat_completions --model-name <model> --model-api-key-env "
                "OPENAI_API_KEY`.",
            )
        )
        return checks
    secrets = report.get("secrets")
    missing_names: list[str] = []
    if isinstance(secrets, Mapping):
        missing = secrets.get("missing_required")
        if isinstance(missing, list):
            missing_names = [str(item) for item in missing]
    checks.append(
        _check(
            "Model",
            "credentials configured",
            not missing_names,
            "secret references resolve"
            if not missing_names
            else f"missing: {', '.join(missing_names)}",
            None
            if not missing_names
            else "Set the environment variable (or secret file), or re-run `agent init`.",
        )
    )
    return checks


def _distributed_runtime_checks(report: Mapping[str, JsonValue] | None) -> list[DoctorCheck]:
    if report is None:
        return []
    runtime = report.get("runtime")
    if not isinstance(runtime, Mapping):
        return []
    enabled: list[str] = []
    for key in ("distributed_queue", "distributed_locks", "distributed_workers"):
        section = runtime.get(key)
        if not isinstance(section, Mapping):
            continue
        backend = str(section.get("backend") or "memory")
        if backend != "memory":
            enabled.append(f"{key}={backend}")
    if not enabled:
        return []
    return [
        DoctorCheck(
            "Advanced",
            "distributed runtime",
            CHECK_SKIPPED,
            "advanced/experimental local distributed backend enabled: " + ", ".join(enabled),
            (
                "Use local distributed mode only for development; choose production "
                "queue/lock/worker backends before HA use."
            ),
        )
    ]


def render_doctor_checks(checks: Sequence[DoctorCheck], *, status: str) -> str:
    lines = ["Universal Agent Doctor", ""]
    current_section: str | None = None
    for check in checks:
        if check.section != current_section:
            current_section = check.section
            lines.append(f"[{check.section}]")
        lines.append(f"{check.symbol} {check.name}: {check.message}")
        if check.hint:
            lines.append(f"  Try: {check.hint}")
    lines.append("")
    lines.append(f"Status: {status}")
    if status == CHECK_OK:
        lines.append('Next: agent run "Hello"')
    else:
        lines.append("Next: fix the checks above, then re-run `agent doctor`.")
    return "\n".join(lines) + "\n"


def doctor_status(checks: Sequence[DoctorCheck]) -> str:
    if any(check.status == CHECK_ERROR for check in checks):
        return "error"
    if all(check.status == CHECK_OK for check in checks):
        return "ok"
    return "warn"


def doctor_body_json(checks: Sequence[DoctorCheck], *, status: str) -> dict[str, object]:
    sections: dict[str, list[dict[str, object]]] = {}
    for check in checks:
        sections.setdefault(check.section, []).append(
            {
                "name": check.name,
                "status": check.status,
                "message": check.message,
                **({"hint": check.hint} if check.hint else {}),
            }
        )
    return {"status": status, "sections": sections}


async def run_doctor_command(args: argparse.Namespace, out: TextIO) -> int:
    checks: list[DoctorCheck] = []
    checks.extend(_environment_checks())
    config_checks, config_path = _config_checks(args)
    checks.extend(config_checks)
    report: Mapping[str, JsonValue] | None = None
    if config_path is not None:
        validity, report = _config_validity_check(config_path)
        checks.append(validity)
    checks.extend(_model_checks(report))
    checks.extend(_distributed_runtime_checks(report))

    if config_path is not None and report is not None:
        # Runtime/Profiles/Domains/Policy come from the embedded agentd runtime —
        # the same initialization path `agent run` uses.
        checks.extend(await _embedded_runtime_checks(args))
    status = doctor_status(checks)
    _emit(args, checks, status, out)
    return _exit_status(args, status)


async def _embedded_runtime_checks(args: argparse.Namespace) -> list[DoctorCheck]:
    from universal_agent_api import AgentdClient, AgentdClientError
    from universal_agent_cli.agentd import _agentd_api_token
    from universal_agent_cli.embedded import EmbeddedRuntimeError, launch_embedded_runtime

    profile_config = cast(str | None, args.profile_config)
    try:
        embedded = launch_embedded_runtime(profile_config)
    except EmbeddedRuntimeError as exc:
        return [
            _check(
                "Runtime",
                "runtime initialization",
                False,
                str(exc),
                "Run `agent config validate --profile-config <path>`, or re-run "
                "`agent init --force` to reset the config.",
            )
        ]
    try:
        async with AgentdClient(
            embedded.base_url,
            bearer_token=_agentd_api_token(args),
        ) as client:
            doctor_report = await client.get_json("/v1/doctor")
            profiles = await client.get_json("/v1/profiles")
            policies = await client.get_json("/v1/policies")
    except AgentdClientError as exc:
        return [
            _check(
                "Runtime",
                "runtime initialization",
                False,
                str(exc),
                "Check the runtime logs or re-run `agent init --force`.",
            )
        ]
    finally:
        embedded.shutdown()

    runtime_checks = _report_checks(doctor_report)
    failed = [str(item.get("name")) for item in runtime_checks if item.get("status") == "error"]
    report_status = str(doctor_report.get("status", "unknown"))
    checks: list[DoctorCheck] = [
        _check(
            "Runtime",
            "runtime initialization",
            not failed,
            f"embedded runtime ready, doctor status {report_status}"
            if not failed
            else f"failed checks: {', '.join(failed)}",
            None if not failed else "Inspect the failed checks with `agent doctor --output json`.",
        ),
        _check(
            "Runtime",
            "persistence",
            True,
            _persistence_summary(doctor_report),
        ),
        _profiles_check(profiles),
        _domains_check(doctor_report),
        _policy_check(policies),
    ]
    return checks


def _profiles_check(profiles: Mapping[str, JsonValue]) -> DoctorCheck:
    profile_names = _body_names(profiles, "profiles", "name")
    return _check(
        "Profiles",
        "profile available",
        bool(profile_names),
        ", ".join(profile_names) if profile_names else "no profiles registered",
        None if profile_names else "Re-run `agent init` to register a profile.",
    )


def _domains_check(doctor_report: Mapping[str, JsonValue]) -> DoctorCheck:
    domain_summary = _check_message(doctor_report, "catalog")
    return _check(
        "Domains",
        "domains initialize",
        bool(domain_summary),
        domain_summary or "no domains active",
        None if domain_summary else "Check the profile domain config; re-run `agent init --force`.",
    )


def _policy_check(policies: Mapping[str, JsonValue]) -> DoctorCheck:
    policy_names = _body_names(policies, "policies", "name")
    return _check(
        "Policy",
        "policy loaded",
        bool(policy_names),
        ", ".join(policy_names) if policy_names else "no policies loaded",
        None if policy_names else "Check the profile domain policy declarations.",
    )


def _report_checks(doctor_report: Mapping[str, JsonValue]) -> list[dict[str, JsonValue]]:
    checks = doctor_report.get("checks")
    if not isinstance(checks, list):
        return []
    return [item for item in checks if isinstance(item, dict)]


def _check_message(doctor_report: Mapping[str, JsonValue], name: str) -> str:
    for item in _report_checks(doctor_report):
        if item.get("name") == name:
            return str(item.get("message", ""))
    return ""


def _persistence_summary(doctor_report: Mapping[str, JsonValue]) -> str:
    summary = _check_message(doctor_report, "state_event_commit")
    return summary or "commit strategy unknown"


def _body_names(body: Mapping[str, JsonValue], list_key: str, name_key: str) -> list[str]:
    items = body.get(list_key)
    if not isinstance(items, list):
        return []
    names = [str(item.get(name_key, "")) for item in items if isinstance(item, dict)]
    return [name for name in names if name]


def _emit(
    args: argparse.Namespace, checks: Sequence[DoctorCheck], status: str, out: TextIO
) -> None:
    if cast(str, args.output) == "json":
        _write_json(out, doctor_body_json(checks, status=status))
        return
    _write_text(out, render_doctor_checks(checks, status=status))


def _exit_status(args: argparse.Namespace, status: str) -> int:
    fail_on = cast(str, args.fail_on)
    if fail_on == "never":
        return 0
    if status == "error":
        return 1 if fail_on in {"error", "warn"} else 0
    if status == "warn":
        return 1 if fail_on == "warn" else 0
    return 0
