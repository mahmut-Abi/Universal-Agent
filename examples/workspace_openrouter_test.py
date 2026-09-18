"""Workspace Domain + OpenRouter Free Model Test.

Validates the full agent loop with a real LLM:
  Goal → Task → Decision → Action → Observation → Evidence → Evaluation

Configuration (environment variables):
  OPENROUTER_API_KEY   required — OpenRouter API key (never hardcode it)
  OPENROUTER_MODEL     optional — pin a specific model for reproducible runs
                       (default: openrouter/free, which routes nondeterministically)

The run prints the model's Decision events for every failure so rejected or
malformed decisions are visible, and reports per-scenario pass rates over
multiple rounds instead of single-run assertions.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import (
    AgentRuntime,
    DomainLoader,
    Goal,
    InMemoryEventSink,
    InMemoryStateStore,
    RuntimeAPI,
    RuntimeBuilder,
    RuntimeService,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.domains.workspace import WorkspaceDomain
from universal_agent.model.openai_adapters import OpenAIChatCompletionsModelAdapter

# ─── Configuration (env-only, no secrets in source) ──────────

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
USER_AGENT = "universal-agent-runtime/0.1.0"
DEFAULT_MODEL = "openrouter/free"
RUNS_PER_SCENARIO = 2


def _require_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        print(
            "ERROR: OPENROUTER_API_KEY is not set.\n"
            "  export OPENROUTER_API_KEY='sk-or-v1-...'\n"
            "This script never reads keys from source files.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return key


def _model_id() -> str:
    return os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


API_KEY = _require_api_key()
MODEL_ID = _model_id()


def build_service(workspace_path: Path) -> RuntimeService:
    """Build a RuntimeService with the WorkspaceDomain and a real LLM."""
    components = RuntimeBuilder().build(DomainLoader().load(WorkspaceDomain(workspace_path)))
    store = InMemoryStateStore()
    events = InMemoryEventSink()

    model = OpenAIChatCompletionsModelAdapter(
        model=MODEL_ID,
        api_key=API_KEY,
        endpoint=OPENROUTER_URL,
        timeout_seconds=90.0,
        response_format="prompt_json",
        extra_headers={
            "User-Agent": USER_AGENT,
            "HTTP-Referer": "https://openrouter.ai",
            "X-Title": "Universal-Agent-Runtime",
        },
    )

    runtime = AgentRuntime(
        model=model,
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "openrouter-free"}),
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )


async def print_failure_diagnostics(service: RuntimeService, run_result: object) -> None:
    """Print the model's decisions and terminal error for a failed run."""
    result = getattr(run_result, "result", run_result)
    status = getattr(result, "status", None)
    if status is not None and getattr(status, "value", None) == "completed":
        return
    error = getattr(result, "error", None)
    error_code = getattr(result, "error_code", None)
    if error or error_code:
        print(f"    error_code: {error_code}")
        print(f"    error: {str(error)[:200]}")
    session = getattr(run_result, "session", None)
    session_id = getattr(session, "session_id", None)
    if session_id is None:
        return
    try:
        events = await service.list_events(session_id)
    except Exception:
        return
    for event in events:
        event_type = getattr(event, "type", None)
        data = getattr(event, "data", {})
        if event_type == "DecisionGenerated":
            print(f"    decision: {str(dict(data))[:250]}")
        elif event_type == "ActionFailed":
            print(f"    action_failed: {str(dict(data))[:250]}")
        elif event_type == "EvaluationCompleted":
            print(f"    evaluation: {str(dict(data))[:200]}")


# ─── Scenario definitions ────────────────────────────────────


async def scenario_inspect(service: RuntimeService, workspace: Path) -> bool:
    goal = Goal(
        "Inspect the workspace and check if it is healthy",
        (SuccessCriterion("healthy", True),),
    )
    task = Task("Inspect", ("healthy",))
    run = await asyncio.wait_for(service.run_goal(goal, task), timeout=120)
    ok = run.result.status.value == "completed"
    print(f"  Status: {run.result.status.value}  Iterations: {run.result.iterations}")
    if not ok:
        await print_failure_diagnostics(service, run)
    return ok


async def scenario_create_file(service: RuntimeService, workspace: Path) -> bool:
    goal = Goal(
        "Create a file called hello.py with content: print('hello world')",
        (SuccessCriterion("created", True),),
    )
    task = Task("Create hello.py", ("created",))
    run = await asyncio.wait_for(service.run_goal(goal, task), timeout=120)
    exists = (workspace / "hello.py").exists()
    ok = run.result.status.value == "completed" and exists
    print(f"  Status: {run.result.status.value}  File exists: {exists}")
    if not ok:
        await print_failure_diagnostics(service, run)
    return ok


async def scenario_read_modify(service: RuntimeService, workspace: Path) -> bool:
    (workspace / "config.txt").write_text("version=1\n")
    goal = Goal(
        "Read config.txt and change the version to 2",
        (SuccessCriterion("modified", True),),
    )
    task = Task("Modify config.txt", ("modified",))
    run = await asyncio.wait_for(service.run_goal(goal, task), timeout=120)
    content = (workspace / "config.txt").read_text() if (workspace / "config.txt").exists() else ""
    ok = run.result.status.value == "completed" and "version=2" in content
    print(f"  Status: {run.result.status.value}  Content: {content.strip()!r}")
    if not ok:
        await print_failure_diagnostics(service, run)
    return ok


async def scenario_search_files(service: RuntimeService, workspace: Path) -> bool:
    (workspace / "a.py").write_text("def hello():\n    pass\n")
    (workspace / "b.py").write_text("def world():\n    pass\n")
    goal = Goal(
        "Search for 'def' in Python files",
        (SuccessCriterion("match_count", 2),),
    )
    task = Task("Search files", ("match_count",))
    run = await asyncio.wait_for(service.run_goal(goal, task), timeout=120)
    ok = run.result.status.value == "completed"
    print(f"  Status: {run.result.status.value}")
    if not ok:
        await print_failure_diagnostics(service, run)
    return ok


SCENARIOS: tuple[tuple[str, Callable[[RuntimeService, Path], Awaitable[bool]]], ...] = (
    ("Inspect Workspace", scenario_inspect),
    ("Create File", scenario_create_file),
    ("Read and Modify", scenario_read_modify),
    ("Search Files", scenario_search_files),
)


async def main() -> None:
    print("=" * 60)
    print("Workspace Domain + OpenRouter Test")
    print(f"Endpoint: {OPENROUTER_URL}")
    print(f"Model: {MODEL_ID}")
    print(f"Runs per scenario: {RUNS_PER_SCENARIO}")
    print("=" * 60)

    # results[scenario] = list of per-round pass booleans
    results: dict[str, list[bool]] = {name: [] for name, _ in SCENARIOS}

    for round_no in range(1, RUNS_PER_SCENARIO + 1):
        print(f"\n──── Round {round_no}/{RUNS_PER_SCENARIO} ────")
        with TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            service = build_service(workspace)
            for name, scenario in SCENARIOS:
                print(f"\n--- {name} ---")
                try:
                    passed = await scenario(service, workspace)
                except TimeoutError:
                    print("  ✗ TIMEOUT")
                    passed = False
                except Exception as exc:
                    print(f"  ✗ ERROR: {type(exc).__name__}: {str(exc)[:150]}")
                    passed = False
                results[name].append(passed)

    print("\n" + "=" * 60)
    print("Summary (per-scenario pass rate)")
    print("=" * 60)
    overall_passes = 0
    overall_runs = 0
    for name, rounds in results.items():
        passed_count = sum(1 for r in rounds if r)
        rate = passed_count / len(rounds) if rounds else 0.0
        marks = " ".join("✓" if r else "✗" for r in rounds)
        print(f"  {name:<20} {passed_count}/{len(rounds)} ({rate:.0%})  [{marks}]")
        overall_passes += passed_count
        overall_runs += len(rounds)
    print(f"\nOverall: {overall_passes}/{overall_runs} ({overall_passes / overall_runs:.0%})")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
