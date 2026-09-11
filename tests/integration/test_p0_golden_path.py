"""P0 Golden Path integration test (spec §17/§20).

Runs the full acceptance flow end-to-end through the real embedded agentd
runtime (no injected service, no real LLM):

    init -> config -> doctor -> run -> session list -> session show

The default profile uses the deterministic FakeModel (``ScriptedModelAdapter``)
and the fake Kubernetes backend, so CI never needs API keys or a cluster.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest

from universal_agent_cli import run_cli

pytestmark = [pytest.mark.integration]


def _read_json(buffer: StringIO) -> dict[str, object]:
    loaded = json.loads(buffer.getvalue())
    assert isinstance(loaded, dict)
    return loaded


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
    assert "kubernetes@0.2.0" in config_text

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

    # 6. session show — human report with goal and timeline.
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
    assert "Goal: Analyze the demo workload" in show_text
    assert "Timeline:" in show_text
    assert "GoalCreated" in show_text
    assert "GoalCompleted" in show_text
    assert "Evidence:" in show_text
    assert "Actions:" in show_text

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
    assert "kubernetes@0.2.0" in show_profile_text


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
