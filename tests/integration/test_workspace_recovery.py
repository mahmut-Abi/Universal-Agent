"""Recovery-path integration tests for the WorkspaceDomain (deterministic).

Fault injection with a scripted model proves the timeout → retry_action
recovery rule actually fires in the full runtime loop, offline and
reproducibly:

1. Transient failure  first tool call times out → RecoveryPlanned → retry
                      succeeds → goal COMPLETED, file created.
2. Exhausted retries  every attempt times out → retry budget spent → goal
                      FAILED, no partial state claimed as success.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DomainLoader,
    ExecutionStatus,
    Goal,
    InMemoryEventSink,
    InMemoryStateStore,
    RuntimeBuilder,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core import JsonMapping
from universal_agent.domains.workspace import CREATE_FILE_CAPABILITY, WorkspaceDomain
from universal_agent.tools import Tool


class FlakyTool:
    """Proxy tool that raises TimeoutError for the first ``fail_times`` calls."""

    def __init__(self, inner: Tool, fail_times: int) -> None:
        self._wrapped = inner
        self._remaining_failures = fail_times
        self.calls = 0
        self.definition = inner.definition

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        self.calls += 1
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise TimeoutError("injected transient failure")
        return await self._wrapped.execute(arguments)


class FlakyWorkspaceDomain(WorkspaceDomain):
    """WorkspaceDomain whose create/modify tools fail transiently at first."""

    def __init__(self, workspace: Path, *, fail_times: int) -> None:
        super().__init__(workspace)
        self._fail_times = fail_times
        self._flaky = FlakyTool(self._tools[3], fail_times)  # create_file tool

    def tools(self) -> tuple[Tool, ...]:
        return (*self._tools[:3], self._flaky, *self._tools[4:])


def create_file_decision(path: str, content: str) -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Create the file",
        capability=CREATE_FILE_CAPABILITY,
        target=f"file/{path}",
        arguments=immutable_json({"path": path, "content": content}),
        expected_observations=("created",),
    )


def finish_decision() -> Decision:
    return Decision(DecisionType.FINISH, "File created and verified")


def run_goal(domain: WorkspaceDomain) -> tuple[AgentRuntime, InMemoryEventSink]:
    components = RuntimeBuilder().build(DomainLoader().load(domain))
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=_ScriptedModel([create_file_decision("recovered.txt", "ok"), finish_decision()]),
        state_store=InMemoryStateStore(),
        components=components,
        event_sink=events,
    )
    return runtime, events


class _ScriptedModel:
    def __init__(self, decisions: list[Decision]) -> None:
        self._decisions = list(decisions)

    async def decide(self, context: object) -> Decision:
        return self._decisions.pop(0)

    def model_usage(self) -> None:
        return None


@pytest.mark.asyncio
async def test_timeout_recovers_via_retry_and_completes() -> None:
    with TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        domain = FlakyWorkspaceDomain(workspace, fail_times=1)
        runtime, events = run_goal(domain)

        result = await runtime.run(
            Goal("Create recovered.txt", (SuccessCriterion("created", True),)),
            Task("Create file", ("created",)),
        )

        assert result.status is ExecutionStatus.COMPLETED
        assert (workspace / "recovered.txt").read_text() == "ok"
        # The tool ran twice: once timed out, once succeeded.
        assert domain._flaky.calls == 2
        event_types = [event.type for event in events.events]
        assert "RecoveryPlanned" in event_types
        assert "RecoveryExhausted" not in event_types
        # The retry path must not consume a new model decision: scripted model
        # had exactly two decisions (create + finish) and both were used.
        assert not _pending_decisions(runtime)


@pytest.mark.asyncio
async def test_exhausted_retry_budget_fails_without_false_success() -> None:
    with TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        domain = FlakyWorkspaceDomain(workspace, fail_times=99)  # always fails
        runtime, events = run_goal(domain)

        result = await runtime.run(
            Goal("Create recovered.txt", (SuccessCriterion("created", True),)),
            Task("Create file", ("created",)),
        )

        assert result.status is ExecutionStatus.FAILED
        assert not (workspace / "recovered.txt").exists()
        event_types = [event.type for event in events.events]
        assert "RecoveryPlanned" in event_types
        assert "RecoveryExhausted" in event_types


def _pending_decisions(runtime: AgentRuntime) -> list[Decision]:
    model = runtime._model
    return list(getattr(model, "_decisions", []))


@pytest.mark.asyncio
async def test_expanded_task_does_not_trap_goal_completion() -> None:
    """Regression: a dynamically expanded task planned after the goal's
    criteria were met must not reject the model's FINISH with INVALID_STATE.

    Sequence: read a missing file (soft failure records exists=false +
    target_file, the expander plans a create task) → create the file
    (evaluation completes the goal) → FINISH must succeed.
    """

    from universal_agent.domains.workspace import INSPECT_FILE_CAPABILITY

    with TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        components = RuntimeBuilder().build(DomainLoader().load(WorkspaceDomain(workspace)))
        events = InMemoryEventSink()
        decisions = [
            Decision(
                DecisionType.EXECUTE,
                "Read the file first",
                capability=INSPECT_FILE_CAPABILITY,
                target="file/ghost.txt",
                arguments=immutable_json({"path": "ghost.txt"}),
                expected_observations=("readable",),
            ),
            create_file_decision("ghost.txt", "now exists"),
            finish_decision(),
        ]
        runtime = AgentRuntime(
            model=_ScriptedModel(decisions),
            state_store=InMemoryStateStore(),
            components=components,
            event_sink=events,
        )
        result = await runtime.run(
            Goal(
                "Ensure ghost.txt exists with content",
                (SuccessCriterion("created", True),),
            ),
            Task("Read then create ghost.txt", ("created",)),
        )

        assert result.status is ExecutionStatus.COMPLETED
        assert (workspace / "ghost.txt").read_text() == "now exists"
        event_types = [event.type for event in events.events]
        # The expander planned the create task from the failed-read evidence.
        assert event_types.count("TaskCreated") == 2
