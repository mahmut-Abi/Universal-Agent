"""P0 Golden Path integration test (spec §17/§20).

Runs the full acceptance flow end-to-end through the real embedded agentd
runtime (no injected service, no real LLM):

    init -> config -> doctor -> run -> session list -> session show

The default profile uses the deterministic local WorkspaceDecisionAdapter and the
read-only local workspace domain, so CI never needs API keys or a cluster.
"""

from __future__ import annotations

import json
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

from universal_agent_cli import run_cli
from universal_agent_cli.parser import build_parser


async def _capture_cli(argv: list[str]) -> tuple[int, str, str]:
    out = StringIO()
    err = StringIO()
    status = await run_cli(argv, stdout=out, stderr=err)
    return status, out.getvalue(), err.getvalue()


def _read_json(buffer: StringIO) -> dict[str, object]:
    loaded = json.loads(buffer.getvalue())
    assert isinstance(loaded, dict)
    return loaded


def test_cli_help_prioritizes_golden_path_and_labels_advanced() -> None:
    help_text = build_parser("agent").format_help()
    init_index = help_text.index("init")
    doctor_index = help_text.index("doctor")
    expected_usage = "usage: agent [options] {init,doctor,run,session,config,profile|advanced...}"
    assert expected_usage in help_text
    assert "Golden Path commands:" in help_text
    assert "Advanced / experimental commands remain available" in help_text
    assert init_index < doctor_index


def test_init_help_groups_first_day_and_advanced_options() -> None:
    # Exercise the real console-script entry (argv[0] == "ua") without
    # depending on `uv` or an installed `ua` shim: CI runners have neither.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.argv[0] = 'ua'; "
                "from universal_agent_cli import main; sys.exit(main())"
            ),
            "init",
            "--help",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    help_text = result.stdout
    assert "First-day options:" in help_text
    assert "Advanced: Kubernetes/domain backend options:" in help_text
    assert "Advanced: distributed runtime options:" in help_text
    assert "usage: ua init [--output PATH]" in help_text


@pytest.mark.asyncio
async def test_p0_golden_path_init_doctor_run_session(tmp_path: Path) -> None:
    config_dir = tmp_path / "universal-agent"
    profile_path = config_dir / "profile.json"
    profile_config = str(profile_path)

    # 1. init — create the golden-path configuration tree.
    init_out = StringIO()
    assert (
        await run_cli(
            ["init", "--output-format", "json", "--output", profile_config],
            stdout=init_out,
        )
        == 0
    )
    init_payload = _read_json(init_out)
    assert init_payload["status"] == "created"
    assert config_dir.joinpath("config.json").is_file()

    # init is idempotent.
    init_again = StringIO()
    assert (
        await run_cli(
            ["init", "--output-format", "json", "--output", profile_config],
            stdout=init_again,
        )
        == 0
    )
    assert _read_json(init_again)["status"] == "reused"

    # 2. config — bare `agent config` renders the human-readable view.
    config_out = StringIO()
    assert await run_cli(["--profile-config", profile_config, "config"], stdout=config_out) == 0
    config_text = config_out.getvalue()
    assert "Universal Agent Configuration" in config_text
    assert "Provider: scripted" in config_text
    assert "Max steps: 20" in config_text
    assert "local@0.1.0" in config_text
    assert "Policy" in config_text
    assert "Discovery order" in config_text

    # 3. doctor — every section passes with the default offline profile.
    doctor_out = StringIO()
    assert (
        await run_cli(
            ["--profile-config", profile_config, "doctor", "--output", "json"],
            stdout=doctor_out,
        )
        == 0
    )
    doctor = _read_json(doctor_out)
    assert doctor["status"] == "ok"
    sections = doctor["sections"]
    assert isinstance(sections, dict)
    for expected in (
        "Environment",
        "Configuration",
        "Model",
        "Runtime",
        "Profiles",
        "Domains",
        "Policy",
    ):
        assert expected in sections
    for section_checks in sections.values():
        assert isinstance(section_checks, list)
        for check in section_checks:
            assert isinstance(check, dict)
            assert check["status"] == "ok", check

    # 4. run — human text summary with Session id and Status.
    run_out = StringIO()
    assert (
        await run_cli(
            [
                "--profile-config",
                profile_config,
                "run",
                "Analyze the demo workload",
                "--success",
                "healthy=true",
                "--output",
                "json",
            ],
            stdout=run_out,
        )
        == 0
    )
    run_payload = _read_json(run_out)
    result = run_payload["result"]
    assert isinstance(result, dict)
    assert result["status"] == "completed"
    session_id = str(result["session_id"])

    text_run_out = StringIO()
    assert (
        await run_cli(
            [
                "--profile-config",
                profile_config,
                "run",
                "Analyze the demo workload",
                "--success",
                "healthy=true",
            ],
            stdout=text_run_out,
        )
        == 0
    )
    run_text = text_run_out.getvalue()
    assert "Agent started" in run_text
    assert "Agent completed" in run_text
    assert "Status: success" in run_text
    assert f"Session: {session_id}" in run_text or "Session: session-" in run_text
    assert "Duration:" in run_text
    assert "Steps:" in run_text
    assert "Tool calls:" in run_text
    assert "Evidence:" in run_text

    # 5. session list — the run created exactly this session (text output).
    list_out = StringIO()
    assert (
        await run_cli(
            ["--profile-config", profile_config, "session", "list", "--output", "json"],
            stdout=list_out,
        )
        == 0
    )
    list_payload = _read_json(list_out)
    sessions = list_payload["sessions"]
    assert isinstance(sessions, list)
    listed_ids = {str(item.get("session_id")) for item in sessions if isinstance(item, dict)}
    assert session_id in listed_ids
    text_list_out = StringIO()
    assert (
        await run_cli(
            ["--profile-config", profile_config, "session", "list"],
            stdout=text_list_out,
        )
        == 0
    )
    assert "SESSION" in text_list_out.getvalue()
    assert session_id in text_list_out.getvalue()

    # 6. session show — human report with summary and counts; raw timeline is separate.
    show_out = StringIO()
    assert (
        await run_cli(
            ["--profile-config", profile_config, "session", "show", session_id],
            stdout=show_out,
        )
        == 0
    )
    show_text = show_out.getvalue()
    assert f"Session: {session_id}" in show_text
    assert "Status: success" in show_text
    assert "Summary" in show_text
    assert "What happened" in show_text
    assert "Goal: Analyze the demo workload" in show_text
    assert "Timeline:" not in show_text
    assert "Evidence:" in show_text
    assert "Actions:" in show_text

    events_out = StringIO()
    assert (
        await run_cli(
            ["--profile-config", profile_config, "session", "events", session_id],
            stdout=events_out,
        )
        == 0
    )
    events_payload = _read_json(events_out)
    event_items = events_payload.get("events")
    assert isinstance(event_items, list)
    assert any(
        isinstance(event, dict) and event.get("type") == "GoalCompleted" for event in event_items
    )

    explain_out = StringIO()
    assert (
        await run_cli(
            ["--profile-config", profile_config, "session", "explain", session_id],
            stdout=explain_out,
        )
        == 0
    )
    explain_text = explain_out.getvalue()
    assert "Error" in explain_text
    assert "Reason" in explain_text
    assert "Try" in explain_text
    assert "Session is not waiting" in explain_text

    explain_missing = StringIO()
    assert (
        await run_cli(
            ["--profile-config", profile_config, "session", "explain", "missing-session"],
            stdout=explain_missing,
        )
        == 0
    )
    assert "Session not found" in explain_missing.getvalue()

    # 7. profile — list and show the default profile.
    profile_list_out = StringIO()
    assert (
        await run_cli(
            ["--profile-config", profile_config, "profile", "list"], stdout=profile_list_out
        )
        == 0
    )
    assert "default" in profile_list_out.getvalue()
    profile_show_out = StringIO()
    assert (
        await run_cli(
            ["--profile-config", profile_config, "profile", "show", "default"],
            stdout=profile_show_out,
        )
        == 0
    )
    show_profile_text = profile_show_out.getvalue()
    assert "Profile: default" in show_profile_text
    assert "local@0.1.0" in show_profile_text
    assert "Model" in show_profile_text
    assert "Policy" in show_profile_text
    assert "Runtime" in show_profile_text


@pytest.mark.asyncio
async def test_agent_init_defaults_to_project_local_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AGENT_CONFIG_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    out = StringIO()

    assert await run_cli(["init", "--output-format", "json"], stdout=out) == 0

    payload = _read_json(out)
    assert payload["path"] == "universal-agent/profile.json"
    assert payload["config"] == "universal-agent/config.json"
    assert (tmp_path / "universal-agent" / "profile.json").is_file()
    assert not (tmp_path / "home" / ".universal-agent" / "profile.json").exists()


@pytest.mark.asyncio
async def test_agent_init_global_writes_user_level_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AGENT_CONFIG_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    out = StringIO()

    assert await run_cli(["init", "--global", "--output-format", "json"], stdout=out) == 0

    payload = _read_json(out)
    assert payload["path"] == str(tmp_path / "home" / ".universal-agent" / "profile.json")
    assert (tmp_path / "home" / ".universal-agent" / "profile.json").is_file()


@pytest.mark.asyncio
async def test_missing_profile_config_reports_clean_error_not_traceback() -> None:
    """Spec §23: missing config must produce a readable error, never a stack trace."""

    out = StringIO()
    err = StringIO()

    status = await run_cli(
        ["--profile-config", "/nonexistent/profile.json", "session", "list"],
        stdout=out,
        stderr=err,
    )

    assert status == 1
    assert "Traceback" not in err.getvalue()
    assert "Traceback" not in out.getvalue()
    payload = _read_json(err)
    error = payload["error"]
    assert isinstance(error, dict)
    assert "profile config not found" in str(error["message"])
    assert "agent init" in str(error["message"])
