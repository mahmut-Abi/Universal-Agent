"""HTTP coverage for the eval and ecosystem command routes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from universal_agent.agentd import AgentdApp
from universal_agent.agentd.server import build_agentd_asgi_app
from universal_agent.core import (
    Decision,
    DecisionType,
    JsonMapping,
    immutable_json,
)
from universal_agent.domain import DomainLoader, RuntimeBuilder
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.domains.kubernetes.cli_runtime import default_profile
from universal_agent.model import ScriptedModelAdapter
from universal_agent.runtime import AgentRuntime, InMemoryEventSink, RuntimeAPI
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore


class Backend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json(
            {
                "resource": "deployment/example",
                "kind": "Deployment",
                "healthy": True,
            }
        )

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({"resource": "deployment/example", "scaled": True})


def build_app(decisions: tuple[Decision, ...] = ()) -> AgentdApp:
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(Backend(), Backend()))
    )
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=store,
        components=components,
        event_sink=events,
    )
    service = RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
        profiles=(default_profile(),),
    )
    return AgentdApp(service)


def write_evaluation_dataset_file(root: Path) -> None:
    suite_path = root / "suites" / "healthy.json"
    suite_path.parent.mkdir(parents=True, exist_ok=True)
    suite_path.write_text(
        json.dumps(
            {
                "name": "file evaluation suite",
                "tags": ["file", "kubernetes"],
                "scenarios": [
                    {
                        "name": "file healthy workload",
                        "kind": "regression",
                        "tags": ["smoke", "file"],
                        "goal": {
                            "description": "Evaluate workload health from file",
                            "success_criteria": {"healthy": True},
                        },
                        "task": {
                            "description": "Inspect workload from file",
                            "required_criteria": ["healthy"],
                        },
                        "expectations": {
                            "expected_status": "completed",
                            "expected_criteria": {"healthy": True},
                            "required_events": ["GoalCompleted", "EvaluationCompleted"],
                            "required_evidence_claims": ["healthy"],
                            "required_capabilities": ["inspect_workload"],
                            "max_actions": 1,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "dataset.json").write_text(
        json.dumps(
            {
                "apiVersion": "agent.nantian.dev/v1alpha1",
                "kind": "EvaluationDataset",
                "metadata": {
                    "name": "kubernetes-remediation",
                    "version": "1.0.0",
                    "description": "Kubernetes remediation evaluation dataset",
                    "author": "Runtime Team",
                    "tags": ["kubernetes", "regression"],
                },
                "domains": [{"name": "kubernetes", "version": "0.2.0"}],
                "suites": [
                    {
                        "name": "healthy",
                        "path": "suites/healthy.json",
                        "tags": ["smoke"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.integration
def test_eval_datasets_route_lists_and_verifies(tmp_path: Path) -> None:
    dataset_root = tmp_path / "datasets" / "kubernetes"
    write_evaluation_dataset_file(dataset_root)
    client = TestClient(build_agentd_asgi_app(build_app()))

    listed = client.post("/v1/eval/datasets", json={"dataset_dir": str(tmp_path / "datasets")})
    payload = listed.json()
    assert listed.status_code == 200
    assert "error" not in payload, payload
    assert payload["datasets"][0]["name"] == "kubernetes-remediation"

    verify = client.post(
        "/v1/eval/datasets",
        json={"dataset_dir": str(tmp_path / "datasets"), "verify": True},
    )
    assert verify.json()["passed"] is True


@pytest.mark.integration
def test_eval_datasets_route_rejects_unknown_dataset(tmp_path: Path) -> None:
    (tmp_path / "datasets").mkdir(parents=True, exist_ok=True)
    client = TestClient(build_agentd_asgi_app(build_app()))

    response = client.post(
        "/v1/eval/dataset",
        json={"name": "database", "dataset_dir": str(tmp_path / "datasets")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"]["type"] == "EvaluationDatasetNotFoundError"
    assert "evaluation dataset not registered" in payload["error"]["message"]


@pytest.mark.integration
def test_eval_run_route_executes_suite(tmp_path: Path) -> None:
    decisions = (
        Decision(
            DecisionType.EXECUTE,
            "Inspect workload",
            capability="inspect_workload",
            target="deployment/example",
            arguments=immutable_json({"name": "example"}),
            expected_observations=("healthy",),
        ),
        Decision(DecisionType.FINISH, "Health verified"),
    )
    report_dir = tmp_path / "reports"
    client = TestClient(build_agentd_asgi_app(build_app(decisions)))

    response = client.post(
        "/v1/eval/run",
        json={
            "profile": "local-kubernetes",
            "report_dir": str(report_dir),
            "kind": ["regression"],
            "tag": ["smoke"],
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["passed"] is True
    assert payload["suite"]["summary"]["scenario_count"] == 1
    assert payload["gate"]["passed"] is True


@pytest.mark.integration
def test_ecosystem_catalog_route_indexes_local_artifacts(tmp_path: Path) -> None:
    dataset_root = tmp_path / "datasets" / "kubernetes"
    write_evaluation_dataset_file(dataset_root)
    client = TestClient(build_agentd_asgi_app(build_app()))

    response = client.post(
        "/v1/ecosystem/catalog",
        json={"dataset_dir": str(tmp_path / "datasets")},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["summary"]["evaluation_dataset_count"] == 1
    assert payload["evaluation_datasets"][0]["name"] == "kubernetes-remediation"
