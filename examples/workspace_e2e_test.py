"""Workspace Domain end-to-end agent loop test (real LLM).

Covers the AGENTS.md §15 integration scenarios end to end with a live model:

1. Normal execution      Goal → Action → Observation → Complete
2. Multi-step execution  inspect → create → verify chains across iterations
3. Confirmation flow     delete → Policy REQUIRE_CONFIRMATION → WAITING →
                         resume(confirmed=True) → execute
4. Confirmation refusal  delete → WAITING → resume(confirmed=False) →
                         CONFIRMATION_REJECTED, tool never runs
5. Read-only enforcement mutation goal in a read-only session is denied
6. Long context          large workspace + big file; decisions stay correct

Usage:
  export OPENROUTER_API_KEY='sk-or-v1-...'
  .venv/bin/python examples/workspace_e2e_test.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import (
    AgentRuntime,
    DomainLoader,
    ExecutionStatus,
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
from universal_agent.core import ErrorCode
from universal_agent.domains.workspace import WorkspaceDomain
from universal_agent.model.openai_adapters import OpenAIChatCompletionsModelAdapter

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
USER_AGENT = "universal-agent-runtime/0.1.0"


def _api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        print("ERROR: set OPENROUTER_API_KEY first.", file=sys.stderr)
        raise SystemExit(1)
    return key


API_KEY = _api_key()


def build_service(workspace: Path, *, read_only: bool = False) -> RuntimeService:
    components = RuntimeBuilder().build(DomainLoader().load(WorkspaceDomain(workspace)))
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    model = OpenAIChatCompletionsModelAdapter(
        model=os.environ.get("OPENROUTER_MODEL", "openrouter/free"),
        api_key=API_KEY,
        endpoint=OPENROUTER_URL,
        timeout_seconds=90.0,
        response_format="prompt_json",
        max_repair_retries=2,
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
        environment=immutable_json({"environment": "e2e"}),
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )


# ── Scenarios ───────────────────────────────────────────────────────────


async def s1_normal_execution(workspace: Path) -> bool:
    """Goal → Action → Observation → Complete (single step)."""
    print("\n[1] Normal execution: inspect")
    service = build_service(workspace)
    run = await asyncio.wait_for(
        service.run_goal(
            Goal("Inspect the workspace and report whether it is healthy",
                 (SuccessCriterion("healthy", True),)),
            Task("Inspect workspace", ("healthy",)),
        ),
        timeout=120,
    )
    ok = run.result.status is ExecutionStatus.COMPLETED
    print(f"    status={run.result.status.value} iterations={run.result.iterations}")
    return ok


async def s2_multi_step(workspace: Path) -> bool:
    """inspect → create → verify across multiple iterations."""
    print("\n[2] Multi-step: create then verify")
    service = build_service(workspace)
    run = await asyncio.wait_for(
        service.run_goal(
            Goal("Create a file named notes.txt containing 'meeting at 3pm'",
                 (SuccessCriterion("created", True),)),
            Task("Create notes.txt", ("created",)),
        ),
        timeout=180,
    )
    ok = run.result.status is ExecutionStatus.COMPLETED and (workspace / "notes.txt").exists()
    print(f"    status={run.result.status.value} file={(workspace / 'notes.txt').exists()}")
    return ok


async def s3_confirmation_approved(workspace: Path) -> bool:
    """delete → REQUIRE_CONFIRMATION → WAITING → confirmed=True → executed."""
    print("\n[3] Confirmation flow (approved)")
    target = workspace / "to_delete.txt"
    target.write_text("obsolete\n")
    service = build_service(workspace)
    run = await asyncio.wait_for(
        service.run_goal(
            Goal("Delete the file to_delete.txt", (SuccessCriterion("deleted", True),)),
            Task("Delete to_delete.txt", ("deleted",)),
        ),
        timeout=180,
    )
    if run.result.status is not ExecutionStatus.WAITING:
        print(f"    expected WAITING, got {run.result.status.value}")
        return False
    print("    paused for confirmation as designed")
    resumed = await asyncio.wait_for(
        service.resume_session(run.result.session_id, confirmed=True),
        timeout=180,
    )
    ok = resumed.result.status is ExecutionStatus.COMPLETED and not target.exists()
    print(f"    resumed status={resumed.result.status.value} file_removed={not target.exists()}")
    return ok


async def s4_confirmation_refused(workspace: Path) -> bool:
    """Refuse every confirmation until the pending-action path triggers
    CONFIRMATION_REJECTED; the file must never be deleted."""
    print("\n[4] Confirmation flow (refused)")
    target = workspace / "keep_me.txt"
    target.write_text("precious\n")
    service = build_service(workspace)
    run = await asyncio.wait_for(
        service.run_goal(
            Goal("Delete the file keep_me.txt", (SuccessCriterion("deleted", True),)),
            Task("Delete keep_me.txt", ("deleted",)),
        ),
        timeout=180,
    )
    # The model may pause via ask_user (no pending action) or via policy
    # confirmation (pending action). Keep refusing; once a pending delete is
    # rejected the runtime must settle with CONFIRMATION_REJECTED.
    current = run
    for _ in range(4):
        if current.result.status is not ExecutionStatus.WAITING:
            break
        view = await service.get_session(current.result.session_id)
        has_pending = view.pending_action is not None
        print(f"    waiting (pending={has_pending}) → refusing")
        current = await asyncio.wait_for(
            service.resume_session(current.result.session_id, confirmed=False),
            timeout=120,
        )
    file_intact = target.exists()
    ok = (
        current.result.status is ExecutionStatus.FAILED
        and current.result.error_code is ErrorCode.CONFIRMATION_REJECTED
        and file_intact
    )
    print(f"    final status={current.result.status.value} "
          f"error={current.result.error_code} file_intact={file_intact}")
    return ok


async def s5_read_only_enforcement(workspace: Path) -> bool:
    """A mutation goal in a read-only session must not touch the filesystem."""
    print("\n[5] Read-only enforcement")
    guard = workspace / "guard.txt"
    guard.write_text("must not change\n")
    service = build_service(workspace)
    run = await asyncio.wait_for(
        service.run_goal(
            Goal("Create a file called hacked.txt", (SuccessCriterion("created", True),)),
            Task("Create hacked.txt", ("created",)),
            read_only=True,
        ),
        timeout=180,
    )
    no_file = not (workspace / "hacked.txt").exists()
    guard_intact = guard.read_text() == "must not change\n"
    ok = no_file and guard_intact
    print(f"    status={run.result.status.value} mutation_blocked={no_file} "
          f"guard_intact={guard_intact}")
    return ok


async def s6_long_context(workspace: Path) -> bool:
    """Large workspace + big file: context stays bounded, decision stays right."""
    print("\n[6] Long context: large workspace")
    for i in range(30):
        (workspace / f"module_{i}.py").write_text(
            f"# module {i}\n" + "\n".join(f"def filler_{i}_{j}(): pass" for j in range(20))
        )
    big = workspace / "big.log"
    big.write_text("log line\n" * 5000)
    service = build_service(workspace)
    run = await asyncio.wait_for(
        service.run_goal(
            Goal("Search Python files for the pattern 'filler_5_' and report matches",
                 (SuccessCriterion("match_count", 20),)),
            Task("Search modules", ("match_count",)),
        ),
        timeout=180,
    )
    ok = run.result.status is ExecutionStatus.COMPLETED
    print(f"    status={run.result.status.value} iterations={run.result.iterations}")
    return ok


SCENARIOS = (
    ("normal execution", s1_normal_execution),
    ("multi-step create", s2_multi_step),
    ("confirmation approved", s3_confirmation_approved),
    ("confirmation refused", s4_confirmation_refused),
    ("read-only enforcement", s5_read_only_enforcement),
    ("long context", s6_long_context),
)


async def main() -> None:
    print("=" * 60)
    print("Workspace Domain E2E Test (real LLM)")
    print("=" * 60)
    results: dict[str, bool] = {}
    for name, scenario in SCENARIOS:
        with TemporaryDirectory() as tmpdir:
            try:
                results[name] = await scenario(Path(tmpdir))
            except TimeoutError:
                print("    ✗ TIMEOUT")
                results[name] = False
            except Exception as exc:
                print(f"    ✗ ERROR: {type(exc).__name__}: {str(exc)[:200]}")
                results[name] = False
        await asyncio.sleep(2.0)

    print("\n" + "=" * 60)
    passed = sum(1 for ok in results.values() if ok)
    for name, ok in results.items():
        print(f"  {'✓' if ok else '✗'} {name}")
    print(f"\n{passed}/{len(results)} scenarios passed")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
