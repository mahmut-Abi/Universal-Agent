"""Workspace Domain Demo — exercises the full agent runtime loop.

This script demonstrates how the WorkspaceDomain exercises every runtime
extension point:
- Multiple capabilities (observation + mutation)
- Dynamic task expansion
- Evidence collection
- World model updates
- Policy enforcement
- Evaluation
- Recovery rules
- Context providers
- Memory records

It uses ScriptedModelAdapter to simulate LLM decisions and runs through
a complete goal -> task -> decision -> action -> observation -> evidence
-> evaluation cycle.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import (
    AgentRuntime,
    Decision,
    DecisionType,
    DomainLoader,
    Goal,
    InMemoryEventSink,
    InMemoryStateStore,
    RuntimeAPI,
    RuntimeBuilder,
    RuntimeService,
    ScriptedModelAdapter,
    SuccessCriterion,
    Task,
    immutable_json,
)
from universal_agent.core import ExecutionStatus
from universal_agent.domains.workspace import WorkspaceDomain
from universal_agent.evaluation.harness import (
    EvaluationQualityGate,
    EvaluationScenario,
    EvaluationScenarioKind,
    EvaluationSuite,
    ScenarioExpectations,
)
from universal_agent.evaluation.recording import FileEvaluationReportStore
from universal_agent.evaluation.runner import EvaluationRunner

# ─── Scripted Decisions ─────────────────────────────────────────────────────


def inspect_workspace() -> Decision:
    """First decision: inspect the workspace."""
    return Decision(
        DecisionType.EXECUTE,
        "Inspect the workspace to understand its structure",
        capability="inspect_workspace",
        target="workspace",
        arguments=immutable_json({}),
        expected_observations=("healthy", "file_count"),
    )


def create_file(path: str, content: str) -> Decision:
    """Decision: create a file."""
    return Decision(
        DecisionType.EXECUTE,
        f"Create file {path}",
        capability="create_file",
        target=f"file/{path}",
        arguments=immutable_json({"path": path, "content": content}),
        expected_observations=("created",),
    )


def read_file(path: str) -> Decision:
    """Decision: read a file."""
    return Decision(
        DecisionType.EXECUTE,
        f"Read file {path}",
        capability="inspect_file",
        target=f"file/{path}",
        arguments=immutable_json({"path": path}),
        expected_observations=("readable", "content"),
    )


def search_files(pattern: str) -> Decision:
    """Decision: search for a pattern."""
    return Decision(
        DecisionType.EXECUTE,
        f"Search for pattern: {pattern}",
        capability="search_files",
        target="workspace",
        arguments=immutable_json({"pattern": pattern, "glob": "*.py"}),
        expected_observations=("match_count",),
    )


def modify_file(path: str, content: str) -> Decision:
    """Decision: modify a file."""
    return Decision(
        DecisionType.EXECUTE,
        f"Modify file {path}",
        capability="modify_file",
        target=f"file/{path}",
        arguments=immutable_json({"path": path, "content": content}),
        expected_observations=("modified",),
    )


def finish() -> Decision:
    """Final decision: goal is complete."""
    return Decision(DecisionType.FINISH, "Required evidence is present")


# ─── Runtime Builder ────────────────────────────────────────────────────────


def build_service(workspace_path: str) -> RuntimeService:
    """Build a RuntimeService with the WorkspaceDomain."""
    components = RuntimeBuilder().build(DomainLoader().load(WorkspaceDomain(Path(workspace_path))))
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(
            [
                inspect_workspace(),
                create_file("hello.py", "print('hello world')"),
                create_file("README.md", "# My Project\n\nA demo project."),
                read_file("hello.py"),
                search_files("hello"),
                finish(),
            ]
        ),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "demo"}),
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )


# ─── Evaluation Scenarios ───────────────────────────────────────────────────


def workspace_demo_suite() -> EvaluationSuite:
    """Define evaluation scenarios for the workspace domain."""

    # Scenario 1: Simple workspace inspection
    inspect_scenario = EvaluationScenario(
        "inspect workspace",
        Goal(
            "Inspect workspace structure",
            (SuccessCriterion("healthy", True),),
        ),
        Task("Inspect workspace", ("healthy",)),
        ScenarioExpectations(
            expected_status=ExecutionStatus.COMPLETED,
            expected_criteria=immutable_json({"healthy": True}),
            required_events=("GoalCompleted",),
            required_evidence_claims=("healthy",),
            required_capabilities=("inspect_workspace",),
            max_actions=1,
        ),
        kind=EvaluationScenarioKind.REGRESSION,
        tags=("smoke", "workspace"),
    )

    # Scenario 2: Create a file
    create_scenario = EvaluationScenario(
        "create file",
        Goal(
            "Create a new file in the workspace",
            (SuccessCriterion("created", True),),
        ),
        Task("Create file", ("created",)),
        ScenarioExpectations(
            expected_status=ExecutionStatus.COMPLETED,
            expected_criteria=immutable_json({"created": True}),
            required_events=("GoalCompleted",),
            required_evidence_claims=("created",),
            required_capabilities=("create_file",),
            max_actions=2,
        ),
        kind=EvaluationScenarioKind.REGRESSION,
        tags=("mutation", "workspace"),
    )

    # Scenario 3: Multi-step workflow
    multi_step_scenario = EvaluationScenario(
        "multi-step workflow",
        Goal(
            "Inspect workspace, create a file, then verify",
            (
                SuccessCriterion("healthy", True),
                SuccessCriterion("created", True),
            ),
        ),
        Task(
            "Inspect, create, and verify",
            ("healthy", "created"),
        ),
        ScenarioExpectations(
            expected_status=ExecutionStatus.COMPLETED,
            expected_criteria=immutable_json(
                {
                    "healthy": True,
                    "created": True,
                }
            ),
            required_events=("GoalCompleted",),
            required_capabilities=(
                "inspect_workspace",
                "create_file",
            ),
            max_actions=3,
        ),
        kind=EvaluationScenarioKind.REGRESSION,
        tags=("multi-step", "workspace"),
    )

    return EvaluationSuite(
        "workspace domain demo",
        (inspect_scenario, create_scenario, multi_step_scenario),
        tags=("workspace",),
    )


# ─── Main ───────────────────────────────────────────────────────────────────


async def main() -> None:
    """Run the workspace domain demo."""
    print("=" * 60)
    print("Workspace Domain Demo")
    print("=" * 60)

    with TemporaryDirectory() as tmpdir:
        print(f"\nWorkspace: {tmpdir}")

        # Build and run the runtime
        print("\n--- Running Agent Runtime ---")
        service = build_service(tmpdir)

        # Run a simple goal
        goal = Goal(
            "Create a demo project",
            (
                SuccessCriterion("healthy", True),
                SuccessCriterion("created", True),
            ),
        )
        task = Task("Set up workspace", ("healthy", "created"))

        run = await service.run_goal(goal, task)

        print(f"\nResult: {run.result.status.value}")
        print(f"Session: {run.session.session_id}")

        # Show the files created
        print("\n--- Files Created ---")
        import os

        for entry in os.scandir(tmpdir):
            print(f"  {entry.name}")

        # Run evaluation
        print("\n--- Running Evaluation ---")
        eval_result = await EvaluationRunner(
            build_service(tmpdir),
            report_store=FileEvaluationReportStore(tmpdir),
        ).run_suite(
            workspace_demo_suite(),
            gate=EvaluationQualityGate(
                min_pass_rate=1.0,
                max_average_actions_per_scenario=3.0,
            ),
        )

        print(f"\nSuite: {eval_result.suite_report.suite_name}")
        print(f"Passed: {eval_result.passed}")
        print(f"Gate: {eval_result.gate_report.passed}")
        print(f"Scenarios: {eval_result.suite_report.summary.scenario_count}")
        print(f"Pass rate: {eval_result.suite_report.summary.pass_rate:.1%}")

        # Show scenario results
        for report in eval_result.suite_report.reports:
            status = "✓" if report.passed else "✗"
            result_status = report.result.status.value
            print(f"  {status} {report.scenario_name}: {result_status}")

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
