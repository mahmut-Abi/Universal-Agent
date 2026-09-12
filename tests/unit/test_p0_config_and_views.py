"""P0 Golden Path unit tests: config discovery, init idempotency, doctor preflight."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent.core import JsonValue
from universal_agent.profile import default_profile_config_path
from universal_agent_cli import run_cli
from universal_agent_cli.doctor import (
    CHECK_ERROR,
    CHECK_OK,
    DoctorCheck,
    _distributed_runtime_checks,
    doctor_status,
    render_doctor_checks,
)
from universal_agent_cli.text_views import (
    render_config_text,
    render_profile_list_text,
    render_profile_show_text,
    render_run_text,
    render_session_explain_text,
    render_session_list_text,
    render_session_not_found_explain_text,
    render_session_show_text,
)

pytestmark = pytest.mark.unit


def test_default_profile_config_path_prefers_project_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    local = tmp_path / "universal-agent" / "profile.json"
    local.parent.mkdir()
    local.write_text("{}", encoding="utf-8")

    resolved = default_profile_config_path({"HOME": str(tmp_path / "home")})
    assert resolved == Path("universal-agent") / "profile.json"
    assert resolved.is_file()  # resolves against the process cwd (tmp_path)


def test_default_profile_config_path_falls_back_to_user_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_CONFIG_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    resolved = default_profile_config_path({"HOME": str(tmp_path / "home")})
    assert resolved == tmp_path / "home" / ".universal-agent" / "profile.json"


def test_default_profile_config_path_honors_agent_config_dir(tmp_path: Path) -> None:
    resolved = default_profile_config_path({"AGENT_CONFIG_DIR": str(tmp_path)})
    assert resolved == tmp_path / "profile.json"


def test_default_init_output_is_project_local_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from universal_agent_cli.defaults import default_init_output_path, global_init_output_path

    monkeypatch.delenv("AGENT_CONFIG_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    environ = {"HOME": str(tmp_path / "home")}

    assert Path(default_init_output_path(environ)) == Path("universal-agent") / "profile.json"
    assert Path(global_init_output_path(environ)) == (
        tmp_path / "home" / ".universal-agent" / "profile.json"
    )


def test_default_init_output_honors_agent_config_dir(tmp_path: Path) -> None:
    from universal_agent_cli.defaults import default_init_output_path

    assert Path(default_init_output_path({"AGENT_CONFIG_DIR": str(tmp_path)})) == (
        tmp_path / "profile.json"
    )


@pytest.mark.asyncio
async def test_init_is_idempotent_and_reuses_existing_config(tmp_path: Path) -> None:
    from io import StringIO

    profile_path = tmp_path / "universal-agent" / "profile.json"
    first = StringIO()
    second = StringIO()

    first_status = await run_cli(
        ["init", "--output-format", "json", "--output", str(profile_path)],
        stdout=first,
    )
    payload_before = json.loads(profile_path.read_text(encoding="utf-8"))

    second_status = await run_cli(
        ["init", "--output-format", "json", "--output", str(profile_path)],
        stdout=second,
    )
    payload_after = json.loads(profile_path.read_text(encoding="utf-8"))

    assert first_status == 0
    assert second_status == 0
    assert json.loads(first.getvalue())["status"] == "created"
    assert json.loads(second.getvalue())["status"] == "reused"
    assert payload_after == payload_before


@pytest.mark.asyncio
async def test_init_force_rewrites_and_keeps_backup(tmp_path: Path) -> None:
    from io import StringIO

    profile_path = tmp_path / "universal-agent" / "profile.json"
    await run_cli(
        ["init", "--output-format", "json", "--output", str(profile_path)],
        stdout=StringIO(),
    )
    original = profile_path.read_text(encoding="utf-8")

    output = StringIO()
    status = await run_cli(
        [
            "init",
            "--output-format",
            "json",
            "--output",
            str(profile_path),
            "--profile",
            "sre",
            "--force",
        ],
        stdout=output,
    )

    payload = json.loads(output.getvalue())
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    backup = profile_path.with_suffix(".json.bak")

    assert status == 0
    assert payload["status"] == "created"
    assert payload["backups"] == [
        str(profile_path.with_suffix(".json.bak")),
        str((tmp_path / "universal-agent" / "config.json").with_suffix(".json.bak")),
    ]
    assert profile["name"] == "sre"
    assert json.loads(backup.read_text(encoding="utf-8")) == json.loads(original)


@pytest.mark.asyncio
async def test_init_writes_readable_config_json(tmp_path: Path) -> None:
    from io import StringIO

    profile_path = tmp_path / "universal-agent" / "profile.json"
    output = StringIO()
    await run_cli(
        ["init", "--output-format", "json", "--output", str(profile_path)],
        stdout=output,
    )

    config = json.loads((tmp_path / "universal-agent" / "config.json").read_text(encoding="utf-8"))

    assert config["profile"] == "default"
    assert config["model"] == {"provider": "scripted", "name": "scripted"}
    assert config["policy"] == {"mode": "safe"}
    assert config["domains"]["local"] == {"enabled": True, "backend": "fake"}
    assert config["runtime"]["max_steps"] == 20


def test_doctor_status_reports_errors_before_warns() -> None:
    checks = [
        DoctorCheck("Environment", "Python", CHECK_OK, "ok"),
        DoctorCheck("Configuration", "config valid", CHECK_ERROR, "broken"),
    ]
    assert doctor_status(checks) == "error"


def test_doctor_render_includes_try_hint_for_failures() -> None:
    checks = [
        DoctorCheck(
            "Model",
            "credentials configured",
            CHECK_ERROR,
            "missing: OPENAI_API_KEY",
            "Set OPENAI_API_KEY or run `agent init`.",
        )
    ]
    rendered = render_doctor_checks(checks, status="error")

    assert "[Model]" in rendered
    assert "✗ credentials configured: missing: OPENAI_API_KEY" in rendered
    assert "Try: Set OPENAI_API_KEY or run `agent init`." in rendered
    assert "Status: error" in rendered


def test_doctor_warns_when_distributed_local_backend_is_enabled() -> None:
    checks = _distributed_runtime_checks(
        {
            "runtime": {
                "distributed_queue": {"backend": "file"},
                "distributed_locks": {"backend": "memory"},
                "distributed_workers": {"backend": "sqlite"},
            }
        }
    )

    assert doctor_status(checks) == "warn"
    rendered = render_doctor_checks(checks, status="warn")
    assert "[Advanced]" in rendered
    assert "advanced/experimental local distributed backend enabled" in rendered
    assert "queue/lock/worker backends before HA use" in rendered


def test_render_run_text_masks_nothing_but_shows_counts_and_next_steps() -> None:
    body: dict[str, JsonValue] = {
        "result": {
            "session_id": "session-1",
            "status": "waiting",
            "reason": "waiting for confirmation",
            "iterations": 3,
        },
        "session": {
            "goal_description": "restart deployment",
            "pending_action": {
                "capability": "scale_workload",
                "target": "deployment/api",
                "arguments": {"current_replicas": 3, "replicas": 2},
            },
        },
    }
    events_body: dict[str, JsonValue] = {
        "events": [
            {"type": "ActionStarted"},
            {"type": "ActionStarted"},
            {"type": "EvidenceRecorded"},
        ]
    }

    rendered = render_run_text(body, duration_seconds=0.42, events_body=events_body)

    assert "Agent started" in rendered
    assert "Session: session-1" in rendered
    assert "Status: waiting" in rendered
    assert "Duration: 0.42s" in rendered
    assert "Steps: 3" in rendered
    assert "Tool calls: 2" in rendered
    assert "Evidence: 1" in rendered
    assert "Confirmation Required" in rendered
    assert "Pending: scale_workload" in rendered
    assert "Target: deployment/api" in rendered
    assert "Before/after: 3 -> 2" in rendered
    assert "Reason: waiting for confirmation" in rendered
    assert "Risk: guarded mutation" in rendered
    assert "Resume: agent session resume session-1 --confirmed true" in rendered


def test_render_run_text_reports_failed_status() -> None:
    body: dict[str, JsonValue] = {
        "result": {
            "session_id": "session-2",
            "status": "failed",
            "reason": "policy denied",
            "iterations": 1,
        },
        "session": {"goal_description": "delete production", "pending_action": None},
    }

    rendered = render_run_text(body, duration_seconds=12.34, events_body=None)

    assert "Status: failed" in rendered
    assert "Reason: policy denied" in rendered
    assert "Duration: 12.3s" in rendered
    assert "Next: agent doctor" in rendered


def test_render_session_list_text_uses_friendly_names() -> None:
    body: dict[str, JsonValue] = {
        "sessions": [
            {
                "session_id": "abc",
                "goal_status": "completed",
                "created_at": "2026-01-01T10:21:00+00:00",
                "goal_description": "Analyze project",
            },
            {
                "session_id": "def",
                "goal_status": "failed",
                "created_at": "2026-01-01T10:30:00+00:00",
                "goal_description": "Break things",
            },
        ]
    }

    rendered = render_session_list_text(body)

    assert "SESSION" in rendered
    assert "STATUS" in rendered
    assert "CREATED" in rendered
    assert "abc" in rendered and "success" in rendered
    assert "def" in rendered and "failed" in rendered
    assert "Analyze project" in rendered


def test_render_profile_list_text_lists_names() -> None:
    assert render_profile_list_text({"profiles": [{"name": "default"}, {"name": "sre"}]}) == (
        "default\nsre\n"
    )


def test_render_config_text_never_shows_secret_values() -> None:
    body: dict[str, JsonValue] = {
        "model": {
            "provider": "openai_chat_completions",
            "name": "gpt-x",
            "api_key_secret": "model_key",
        },
        "store": {"backend": "file", "path": "/tmp/store"},
        "limits": {"max_iterations": 30, "max_recovery_steps": 8},
        "domains": [{"name": "kubernetes", "version": "0.2.0", "primary": True, "backend": "fake"}],
        "secrets": [{"key": "OPENAI_API_KEY", "available": True}],
    }

    rendered = render_config_text(
        body,
        profile_config_path="/cfg/profile.json",
        config_dir="/cfg",
        active_profile="default",
        policies_body={"policies": [{"name": "safe-mode", "effect": "allow"}]},
    )

    assert "Active profile: default" in rendered
    assert "Provider: openai_chat_completions" in rendered
    assert "API key secret: model_key" in rendered
    assert "Store: file at " in rendered
    assert rendered.endswith("~/.universal-agent/profile.json\n")
    assert "Policy" in rendered and "safe-mode" in rendered
    assert "OPENAI_API_KEY: configured" in rendered
    assert "Discovery order" in rendered
    assert "sk-" not in rendered
    assert "secret-value" not in rendered
    assert "/cfg/profile.json" in rendered


def test_render_profile_show_text_includes_runtime_and_policy() -> None:
    rendered = render_profile_show_text(
        {
            "name": "default",
            "version": "0.1.0",
            "description": "Local profile",
            "domains": [{"name": "local", "version": "0.1.0"}],
        },
        runtime_body={
            "model": {"provider": "scripted", "name": "scripted"},
            "store": {"backend": "file", "path": "/tmp/store"},
            "limits": {"max_iterations": 20},
        },
        policies_body={"policies": [{"name": "local-read-only", "effect": "allow"}]},
    )

    assert "Profile: default" in rendered
    assert "Model" in rendered and "Provider: scripted" in rendered
    assert "local@0.1.0" in rendered
    assert "Policy" in rendered and "local-read-only" in rendered
    assert "Runtime" in rendered and "Max steps: 20" in rendered


def test_render_session_show_text_is_human_first() -> None:
    rendered = render_session_show_text(
        {
            "session_id": "session-1",
            "goal_status": "completed",
            "goal_description": "Hello",
            "current_task_description": "Run goal",
            "current_task_status": "completed",
            "domain_name": "local",
            "domain_version": "0.1.0",
            "termination_reason": "workspace inspection satisfied",
        },
        {
            "events": [
                {"type": "ActionStarted", "occurred_at": "2026-01-01T00:00:00+00:00"},
                {"type": "EvidenceRecorded", "occurred_at": "2026-01-01T00:00:01+00:00"},
            ]
        },
    )

    assert rendered.startswith("Summary\n")
    assert "What happened" in rendered
    assert "Timeline:" not in rendered
    assert "Raw timeline: agent session events session-1" in rendered
    assert "Evidence: 1" in rendered
    assert "Actions: 1" in rendered


def test_render_session_explain_text_covers_common_cases() -> None:
    waiting = render_session_explain_text(
        {
            "session_id": "session-1",
            "goal_status": "waiting",
            "pending_action": {"capability": "scale_workload"},
        }
    )
    missing = render_session_explain_text(
        {
            "session_id": "session-2",
            "goal_status": "failed",
            "termination_reason": "API key missing",
        }
    )
    completed = render_session_explain_text({"session_id": "session-3", "goal_status": "completed"})
    not_found = render_session_not_found_explain_text("missing")

    assert "Error" in waiting and "Confirmation required" in waiting and "Try" in waiting
    assert "Missing model credentials" in missing
    assert "Session is not waiting" in completed
    assert "Session not found" in not_found
