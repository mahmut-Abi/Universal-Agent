"""End-to-end evaluation suite run for the workspace domain (deterministic).

The suite factory is registered via entry points; this test proves the
registered scenarios actually pass through the EvaluationRunner with a
quality gate — not just that the structure is well-formed:

1. healthy workspace  inspect decision → COMPLETED
2. create file        create decision → COMPLETED
3. sensitive path     create .env decision → POLICY_DENIED, zero actions
"""

from __future__ import annotations

from tempfile import TemporaryDirectory

import pytest

from universal_agent import (
    Decision,
    DecisionType,
    DomainLoader,
    Goal,
    RuntimeBuilder,
    ScriptedModelAdapter,
    immutable_json,
)
from universal_agent.core import ExecutionStatus, Goal
from universal_agent.domains.workspace import (
    CREATE_FILE_CAPABILITY,
    INSPECT_WORKSPACE_CAPABILITY,
    WorkspaceDomain,
    build_workspace_evaluation_suite,
)
from universal_agent.evaluation.harness import EvaluationQualityGate
from universal_agent.evaluation.recording import FileEvaluationReportStore
from universal_agent.evaluation.runner import EvaluationRunner
from universal_agent.runtime import AgentRuntime, InMemoryEventSink, RuntimeAPI
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore


def _inspect() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect the workspace",
        capability=INSPECT_WORKSPACE_CAPABILITY,
        target="workspace",
        arguments=immutable_json({}),
        expected_observations=("healthy",),
    )


def _create(path: str, content: str) -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        f"Create {path}",
        capability=CREATE_FILE_CAPABILITY,
        target=f"file/{path}",
        arguments=immutable_json({"path": path, "content": content}),
        expected_observations=("created",),
    )


def _finish() -> Decision:
    return Decision(DecisionType.FINISH, "criteria satisfied")


def build_service(workspace_root: str) -> RuntimeService:
    from pathlib import Path

    components = RuntimeBuilder().build(
        DomainLoader().load(WorkspaceDomain(Path(workspace_root)))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [_inspect(), _finish(), _create("notes.txt", "hi"), _finish(), _create(".env", "K=1")]
        ),
        state_store=store,
        components=components,
        event_sink=events,
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workspace_suite_runs_through_evaluation_runner() -> None:
    suite = build_workspace_evaluation_suite("workspace-e2e-suite")
    assert len(suite.scenarios) == 3

    with TemporaryDirectory() as tmpdir:
        runner = EvaluationRunner(
            build_service(tmpdir),
            report_store=FileEvaluationReportStore(tmpdir),
        )
        result = await runner.run_suite(
            suite,
            gate=EvaluationQualityGate(min_pass_rate=1.0),
        )

        assert result.suite_report.passed, [
            (r.scenario_name, r.failed_checks) for r in result.suite_report.reports
        ]
        assert result.gate_report.passed
        assert result.passed

        # Stored and reloadable.
        stored = FileEvaluationReportStore(tmpdir).load("workspace-e2e-suite")
        assert stored.summary.scenario_count == 3

        # Scenario-level expectations held: two completions, one policy denial.
        statuses = {r.scenario_name: r.result.status for r in result.suite_report.reports}
        assert statuses["healthy workspace"] is ExecutionStatus.COMPLETED
        assert statuses["create file"] is ExecutionStatus.COMPLETED
        assert statuses["sensitive path policy denial"] is ExecutionStatus.FAILED
