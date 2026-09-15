"""Facade + policy + session persistence integration coverage (P0 spec §14/§21/§22).

- The `Agent` facade runs a goal through the full Runtime without Kernel knowledge.
- Policy denial prevents tool execution (`ActionStarted` count proves it).
- Confirmation-required mutations pause; human confirmation resumes through the
  Runtime-owned path; a rebuilt runtime (same session store) completes the goal.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import cast

import pytest

from universal_agent import (
    Agent,
    AgentConfigurationError,
    DomainConfig,
    RuntimeConfig,
    RuntimeHost,
    RuntimeLimitsConfig,
    RuntimeService,
    ScriptedModelAdapter,
    StoreConfig,
    immutable_json,
)
from universal_agent.core import Decision, DecisionType, JsonMapping
from universal_agent.domains.kubernetes import (
    KubernetesBackend,
    KubernetesMutationBackend,
    KubernetesRemediationDomain,
)
from universal_agent_cli import run_cli

pytestmark = pytest.mark.integration


def _build_service(
    backend: object,
    decisions: list[Decision],
    *,
    store_path: Path | None = None,
    environment: str = "staging",
) -> RuntimeService:
    store = StoreConfig.file(str(store_path)) if store_path is not None else StoreConfig.memory()
    config = RuntimeConfig(
        environment=immutable_json({"environment": environment}),
        store=store,
        limits=RuntimeLimitsConfig(max_iterations=12, max_recovery_steps=4),
        domain=DomainConfig("kubernetes", "0.2.0"),
    )
    host = RuntimeHost.build(
        config=config,
        model=ScriptedModelAdapter(decisions),
        domain=KubernetesRemediationDomain(
            cast("KubernetesBackend", backend),
            cast("KubernetesMutationBackend", backend),
        ),
    )
    return host.service


class _FacadeBackend:
    """Fake Kubernetes backend: unhealthy workload, then healthy after scale."""

    def __init__(self) -> None:
        self.inspect_calls: list[str] = []
        self.mutation_calls = 0
        self._scaled = False

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls.append(capability)
        if capability == "inspect_workload":
            if self._scaled:
                return immutable_json(
                    {
                        "resource": "deployment/example",
                        "healthy": True,
                        "desired_replicas": 3,
                        "ready_replicas": 3,
                        "verification_observed": True,
                    }
                )
            return immutable_json(
                {
                    "resource": "deployment/example",
                    "healthy": False,
                    "desired_replicas": 3,
                    "ready_replicas": 1,
                    "resource_version": "rv-before",
                }
            )
        if capability == "inspect_pod":
            return immutable_json(
                {
                    "resource": "pod/example-1",
                    "namespace": "default",
                    "root_cause": "under_replicated",
                }
            )
        raise AssertionError(f"unexpected capability: {capability}")

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        self.mutation_calls += 1
        self._scaled = True
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def _inspect(capability: str, *observations: str) -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        f"Run {capability}",
        capability=capability,
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=observations,
    )


def _scale() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Scale the workload",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 3}),
        expected_observations=("mutation_applied",),
    )


def _invalid_scale() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Attempt invalid scale",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 0}),
        expected_observations=("mutation_applied",),
    )


def _finish() -> Decision:
    return Decision(DecisionType.FINISH, "Health verified")


async def _event_types(agent: Agent, session_id: str) -> list[str]:
    batch = await agent.events(session_id)
    return [event.type for event in batch.events]


async def _write_profile_config(tmp_path: Path, store_path: Path) -> Path:
    profile_path = tmp_path / "universal-agent" / "profile.json"
    output = StringIO()
    status = await run_cli(
        [
            "init",
            "--output-format",
            "json",
            "--output",
            str(profile_path),
            "--store-backend",
            "file",
            "--store-path",
            str(store_path),
        ],
        stdout=output,
    )
    assert status == 0
    return profile_path


@pytest.mark.asyncio
async def test_facade_runs_goal_and_records_session(tmp_path: Path) -> None:
    backend = _FacadeBackend()
    store_path = tmp_path / "store"
    profile_path = await _write_profile_config(tmp_path, store_path)
    service = _build_service(
        backend,
        [
            _inspect("inspect_workload", "healthy"),
            _inspect("inspect_pod", "root_cause"),
            _scale(),
            _inspect("inspect_workload", "verification_observed", "healthy"),
            _finish(),
        ],
        store_path=store_path,
    )
    agent = Agent(service, profile="default")

    result = await agent.run(
        "Restore workload health",
        success_criteria={"healthy": True},
        task_required_criteria=(),
    )

    assert result.status == "completed"
    assert backend.mutation_calls == 1
    events = await _event_types(agent, result.session_id)
    assert "GoalCreated" in events
    assert "PolicyChecked" in events
    assert "ActionStarted" in events
    assert "EvidenceRecorded" in events
    assert "GoalCompleted" in events

    sessions = await agent.sessions()
    assert result.session_id in {str(item.session_id) for item in sessions}
    view = await agent.session(result.session_id)
    assert view.goal_status.value == "completed"

    # The CLI reads the same persisted sessions over the same store.
    list_out = StringIO()
    status = await run_cli(
        ["--profile-config", str(profile_path), "session", "list"],
        stdout=list_out,
    )
    assert status == 0
    assert str(result.session_id) in list_out.getvalue()


@pytest.mark.asyncio
async def test_policy_denies_invalid_mutation_before_tool_execution(tmp_path: Path) -> None:
    """Spec §21: the model requests a dangerous action; policy denies; the tool
    MUST NOT execute."""

    backend = _FacadeBackend()
    service = _build_service(
        backend,
        [
            _inspect("inspect_workload", "healthy"),
            _inspect("inspect_pod", "root_cause"),
            _invalid_scale(),
        ],
    )
    agent = Agent(service)

    result = await agent.run(
        "Restore workload health",
        success_criteria={"healthy": True},
        task_required_criteria=(),
    )

    assert result.status == "failed"
    assert result.error_code == "policy_denied"
    assert backend.mutation_calls == 0
    events = await _event_types(agent, result.session_id)
    assert events.count("ActionStarted") == 2
    assert "GoalCompleted" not in events


@pytest.mark.asyncio
async def test_confirmation_pauses_then_rebuilt_runtime_resumes(tmp_path: Path) -> None:
    """Spec §21/§22: production mutation waits for the human; a rebuilt runtime
    over the same persistent store resumes the SAME session and completes it."""

    backend = _FacadeBackend()
    store_path = tmp_path / "store"
    first_service = _build_service(
        backend,
        [
            _inspect("inspect_workload", "healthy"),
            _inspect("inspect_pod", "root_cause"),
            _scale(),
        ],
        store_path=store_path,
        environment="production",
    )
    agent = Agent(first_service)

    waiting = await agent.run(
        "Restore workload health",
        success_criteria={"healthy": True},
        task_required_criteria=(),
    )

    assert waiting.status == "waiting"
    assert backend.mutation_calls == 0

    # Simulate a process restart: a brand-new Runtime over the same store can
    # see the persisted session and resume it after human confirmation.
    profile_path = await _write_profile_config(tmp_path, store_path)
    list_out = StringIO()
    status = await run_cli(
        ["--profile-config", str(profile_path), "session", "list", "--output", "json"],
        stdout=list_out,
    )
    assert status == 0
    assert waiting.session_id in list_out.getvalue()

    resumed_service = _build_service(
        backend,
        [
            _inspect("inspect_workload", "verification_observed", "healthy"),
            _finish(),
        ],
        store_path=store_path,
        environment="production",
    )
    resumed_agent = Agent(resumed_service)
    completed = await resumed_agent.resume(waiting.session_id, confirmed=True)

    assert completed.status == "completed"
    assert backend.mutation_calls == 1
    view = await resumed_agent.session(waiting.session_id)
    assert view.goal_status.value == "completed"


@pytest.mark.asyncio
async def test_facade_from_profile_requires_existing_config(tmp_path: Path) -> None:
    with pytest.raises(AgentConfigurationError):
        Agent.from_profile("default", config_path=tmp_path / "missing.json")


@pytest.mark.asyncio
async def test_facade_from_profile_runs_local_default_profile(tmp_path: Path) -> None:
    """P0 spec §14/§26 + UA-AUDIT-001 regression: `Agent.from_profile("default")`

    must work for the domain-neutral local profile created by `agent init`.
    The facade used to fall back to the Kubernetes builder and fail with
    ``configured domain local does not match kubernetes``.
    """

    profile_path = await _write_profile_config(tmp_path, tmp_path / "store")

    agent = Agent.from_profile("default", config_path=profile_path)
    result = await agent.run("Analyze the demo workload", success_criteria={"healthy": True})

    assert result.status == "completed"
    events = await _event_types(agent, result.session_id)
    assert "GoalCreated" in events
    assert "GoalCompleted" in events
    # Domain-neutral default: the local workspace domain serves the run and no
    # Kubernetes capability is ever resolved (UA-TEST-002 expectation).
    batch = await agent.events(result.session_id)
    capabilities = [
        str(event.data.get("capability", ""))
        for event in batch.events
        if "capability" in event.data
    ]
    assert capabilities, "the scripted local run must resolve at least one capability"
    assert all("kubernetes" not in capability.lower() for capability in capabilities)

    sessions = await agent.sessions()
    assert result.session_id in {str(item.session_id) for item in sessions}
