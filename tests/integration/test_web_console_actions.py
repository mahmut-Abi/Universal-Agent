"""End-to-end console operator action coverage.

Drives the web console action routes through the real ASGI app and asserts
the same policy/confirmation boundaries the CLI and agentd enforce: pending
actions require explicit confirmation, rejection never executes the tool, and
invalid transitions surface as structured JSON errors instead of silent
no-ops. The console frontend itself is a static single-page client served
from the universal_agent_web package.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from universal_agent.agentd import AgentdApp
from universal_agent.agentd.server import build_agentd_asgi_app
from universal_agent.core import (
    AgentState,
    CapabilityCategory,
    CapabilityDefinition,
    ContextFragment,
    Decision,
    DecisionType,
    DomainManifest,
    DomainMetadata,
    EvaluationContext,
    EvaluationResult,
    EvaluationStatus,
    JsonMapping,
    JsonValue,
    PolicyEffect,
    SideEffect,
    ToolDefinition,
    immutable_json,
)
from universal_agent.domain import DomainLoader, RuntimeBuilder
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import EvidenceExtractor
from universal_agent.memory import MemoryRecord
from universal_agent.model import ScriptedModelAdapter
from universal_agent.policy import Policy, PolicyRule
from universal_agent.recovery import RecoveryRule
from universal_agent.runtime import (
    AgentRuntime,
    InMemoryEventSink,
    RuntimeAPI,
)
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore
from universal_agent.tasks import TaskExpander
from universal_agent.tools import Tool
from universal_agent.world import WorldUpdater


class MutationTool:
    definition = ToolDefinition(
        "change_setting",
        "Change a setting",
        ("change_setting",),
        side_effect=SideEffect.REVERSIBLE,
    )

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        self.calls += 1
        return immutable_json({"changed": True})


class MutationEvaluator:
    name = "mutation-evaluator"

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        complete = context.satisfied_criteria.get("changed") is True
        return EvaluationResult(
            EvaluationStatus.COMPLETED if complete else EvaluationStatus.INCOMPLETE,
            "setting changed" if complete else "setting unchanged",
            self.name,
            immutable_json({"changed": complete}),
            complete,
            complete,
        )


class MutationContextProvider:
    name = "mutation-context"

    def provide(self, state: AgentState) -> tuple[ContextFragment, ...]:
        return ()


class MutationDomain:
    def __init__(self, tool: MutationTool, effect: PolicyEffect) -> None:
        self._tool = tool
        self._effect = effect

    @property
    def manifest(self) -> DomainManifest:
        return DomainManifest(
            "agent.nantian.dev/v1alpha1",
            "Domain",
            DomainMetadata("mutation-test", "1.0.0", "Mutation test domain"),
            ("Setting",),
            ("change_setting",),
            (MutationEvaluator.name,),
        )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition(
                "change_setting",
                "Change setting",
                CapabilityCategory.MUTATION,
            ),
        )

    def tools(self) -> tuple[Tool, ...]:
        return (self._tool,)

    def policies(self) -> tuple[Policy, ...]:
        return (
            PolicyRule(
                "mutation-policy",
                self._effect,
                "mutation requires policy decision",
                capabilities=("change_setting",),
            ),
        )

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (MutationEvaluator(),)

    def context_providers(self) -> tuple[MutationContextProvider, ...]:
        return (MutationContextProvider(),)

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
        return ()

    def world_updaters(self) -> tuple[WorldUpdater, ...]:
        return ()

    def task_expanders(self) -> tuple[TaskExpander, ...]:
        return ()

    def recovery_rules(self) -> tuple[RecoveryRule, ...]:
        return ()

    def memories(self) -> tuple[MemoryRecord, ...]:
        return ()


def change_setting_decision() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Change the setting",
        capability="change_setting",
        target="setting/example",
        arguments=immutable_json({"value": "new"}),
        expected_observations=("changed",),
    )


def finish_decision() -> Decision:
    return Decision(DecisionType.FINISH, "The setting changed")


def build_mutation_app(effect: PolicyEffect) -> tuple[AgentdApp, MutationTool]:
    tool = MutationTool()
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    components = RuntimeBuilder().build(DomainLoader().load(MutationDomain(tool, effect)))
    runtime = AgentRuntime(
        model=ScriptedModelAdapter([change_setting_decision(), finish_decision()]),
        state_store=store,
        components=components,
        event_sink=events,
    )
    service = RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
    )
    return AgentdApp(service), tool


def goal_body() -> dict[str, JsonValue]:
    return {
        "goal": {
            "description": "Change the setting",
            "success_criteria": [{"key": "changed", "expected": True}],
        },
        "task": {"description": "Change setting", "required_criteria": ["changed"]},
    }


def waiting_session_id(client: TestClient) -> str:
    created = client.post("/v1/sessions", json=goal_body())
    assert created.status_code == 201
    session_id = created.json()["result"]["session_id"]
    assert isinstance(session_id, str)
    return session_id


@pytest.mark.behavior
def test_console_resume_confirm_executes_pending_action() -> None:
    app, tool = build_mutation_app(PolicyEffect.REQUIRE_CONFIRMATION)

    with TestClient(build_agentd_asgi_app(app)) as client:
        session_id = waiting_session_id(client)

        page = client.get(f"/console/sessions/{session_id}")
        assert page.status_code == 200
        assert "text/html" in page.headers["content-type"]

        response = client.post(
            f"/console/sessions/{session_id}/resume",
            json={"confirmed": True},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["run"]["result"]["status"] == "completed"

        assert tool.calls == 1
        session = client.get(f"/v1/sessions/{session_id}").json()
        assert session["goal_status"] == "completed"


@pytest.mark.behavior
def test_console_resume_reject_never_executes_pending_action() -> None:
    app, tool = build_mutation_app(PolicyEffect.REQUIRE_CONFIRMATION)

    with TestClient(build_agentd_asgi_app(app)) as client:
        session_id = waiting_session_id(client)

        response = client.post(
            f"/console/sessions/{session_id}/resume",
            json={"confirmed": False},
        )

        assert response.status_code == 200
        payload = response.json()
        result = payload["run"]["result"]
        assert result["status"] == "failed"
        assert result["error_code"] == "confirmation_rejected"
        assert tool.calls == 0
        session = client.get(f"/v1/sessions/{session_id}").json()
        assert session["goal_status"] == "failed"


@pytest.mark.behavior
def test_console_resume_without_confirmation_reports_required_boundary() -> None:
    app, tool = build_mutation_app(PolicyEffect.REQUIRE_CONFIRMATION)

    with TestClient(build_agentd_asgi_app(app)) as client:
        session_id = waiting_session_id(client)

        response = client.post(f"/console/sessions/{session_id}/resume", json={})

        assert response.status_code == 200
        payload = response.json()
        result = payload["run"]["result"]
        assert result["status"] == "failed"
        assert result["error_code"] == "invalid_state"
        assert "resume requires confirmation for pending action" in result["reason"]
        assert tool.calls == 0


@pytest.mark.behavior
def test_console_pause_and_cancel_round_trip() -> None:
    app, _ = build_mutation_app(PolicyEffect.REQUIRE_CONFIRMATION)

    with TestClient(build_agentd_asgi_app(app)) as client:
        session_id = waiting_session_id(client)

        paused = client.post(
            f"/console/sessions/{session_id}/pause",
            json={"reason": "ops freeze"},
        )
        assert paused.status_code == 200
        assert paused.json()["run"]["result"]["status"] == "waiting"
        session = client.get(f"/v1/sessions/{session_id}").json()
        assert session["goal_status"] == "waiting"
        assert session["termination_reason"] == "ops freeze"

        cancelled = client.post(
            f"/console/sessions/{session_id}/cancel",
            json={"reason": "abandoned"},
        )
        assert cancelled.status_code == 200
        session = client.get(f"/v1/sessions/{session_id}").json()
        assert session["goal_status"] == "cancelled"

        final_page = client.get(f"/console/sessions/{session_id}")
        assert "Operator actions" not in final_page.text


@pytest.mark.contract
def test_console_action_routes_reject_unknown_sessions_and_get_method() -> None:
    app, _ = build_mutation_app(PolicyEffect.REQUIRE_CONFIRMATION)

    with TestClient(build_agentd_asgi_app(app)) as client:
        missing = client.post("/console/sessions/session-missing/resume", json={})
        assert missing.status_code == 404

        session_id = waiting_session_id(client)
        wrong_method = client.get(f"/console/sessions/{session_id}/resume")
        assert wrong_method.status_code == 405
