from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import fromstring

import pytest

from universal_agent import (
    AgentProfile,
    AgentRuntime,
    Decision,
    DecisionType,
    DistributedRuntimeCoordinator,
    DomainConfig,
    DomainLoader,
    Goal,
    InMemoryEventSink,
    InMemoryStateStore,
    ModelConfig,
    ModelUsage,
    ProfileConfig,
    RuntimeAPI,
    RuntimeBuilder,
    RuntimeConfig,
    RuntimeLimitsConfig,
    RuntimeService,
    ScriptedModelAdapter,
    SecretRef,
    StoreConfig,
    SuccessCriterion,
    Task,
    WorkerId,
    immutable_json,
    load_ecosystem_registry_manifest,
)
from universal_agent.agentd import AgentdHttpServer
from universal_agent.cli import run_cli
from universal_agent.core import DomainIdentity, JsonMapping, SessionId
from universal_agent.domain import (
    DomainPackage,
    DomainPackageCompatibility,
    DomainPackageManifest,
    DomainPackageRegistry,
)
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.evaluation.recording import (
    FileEvaluationReportStore,
    FileReplayRecordingStore,
    encode_evaluation_report,
)


class CliBackend:
    def __init__(self) -> None:
        self.inspect_calls = 0
        self.mutation_calls = 0

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls += 1
        assert capability == "inspect_workload"
        return immutable_json(
            {
                "resource": "deployment/example",
                "healthy": True,
                "kind": "Deployment",
                "relation:owns": ["pod/example-1"],
            }
        )

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.mutation_calls += 1
        assert capability == "scale_workload"
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def inspect_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload through CLI test service",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=("healthy",),
    )


def scale_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Scale workload through CLI test service",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 3}),
        expected_observations=("mutation_applied",),
    )


def invalid_scale_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Invalid scale through CLI test service",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 0}),
        expected_observations=("mutation_applied",),
    )


def wait() -> Decision:
    return Decision(DecisionType.WAIT, "CLI test waiting point")


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "CLI test finished")


def goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Verify workload from CLI", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )


def cli_profile() -> AgentProfile:
    domain = DomainConfig("kubernetes", "0.2.0")
    return AgentProfile(
        "production-operator",
        "1.0.0",
        "Production Kubernetes operator",
        domain,
        RuntimeConfig(environment=immutable_json({"environment": "staging"}), domain=domain),
        (domain,),
    )


def build_cli_service(
    decisions: list[Decision],
    *,
    usage: list[ModelUsage] | None = None,
    distributed_coordinator: DistributedRuntimeCoordinator | None = None,
    domain_packages: DomainPackageRegistry | None = None,
    environment: str = "staging",
) -> tuple[RuntimeService, CliBackend]:
    backend = CliBackend()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions, usage=usage or ()),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": environment}),
    )
    api = RuntimeAPI(runtime=runtime, session_store=store, event_reader=events)
    config = RuntimeConfig(
        environment=immutable_json({"environment": environment}),
        store=StoreConfig.memory(),
        limits=RuntimeLimitsConfig(max_iterations=12, max_recovery_steps=4),
        domain=DomainConfig("kubernetes", "0.2.0"),
    )
    return RuntimeService(
        runtime_api=api,
        components=components,
        profiles=(cli_profile(),),
        config=config,
        distributed_coordinator=distributed_coordinator,
        domain_packages=domain_packages,
    ), backend


def package_registry() -> DomainPackageRegistry:
    package = DomainPackage(
        manifest=DomainPackageManifest(
            api_version="agent.nantian.dev/v1alpha1",
            kind="DomainPackage",
            name="kubernetes",
            version="0.2.0",
            description="Packaged Kubernetes runtime metadata",
            author="Runtime Team",
            entrypoint="universal_agent.domains.kubernetes:KubernetesRemediationDomain",
            ontology=("Deployment", "Pod"),
            capabilities=("inspect_workload", "scale_workload"),
            tools=("kubernetes_inspect_workload", "kubernetes_scale_workload"),
            policies=("kubernetes-scale-safety",),
            procedures=("diagnose_unhealthy_workload",),
            knowledge=("kubernetes readiness",),
            evaluators=("workload-health",),
            context_providers=("kubernetes_context",),
            resources=("resources/runbook.md",),
            dependencies=(DomainIdentity("observability", "1.0.0"),),
            required_tools=("kubernetes_api",),
            compatibility=DomainPackageCompatibility(
                runtime_api=">=0.1,<1",
                domain_api="agent.nantian.dev/v1alpha1",
            ),
            security=immutable_json({"side_effects": "reversible"}),
            tags=("kubernetes", "ops"),
        ),
        root_path=Path("/domains/kubernetes"),
        manifest_path=Path("/domains/kubernetes/manifest.json"),
    )
    return DomainPackageRegistry((package,))


def read_json(buffer: StringIO) -> dict[str, Any]:
    loaded: object = json.loads(buffer.getvalue())
    assert isinstance(loaded, dict)
    return loaded


def write_evaluation_suite_file(
    path: Path,
    *,
    quality_gate: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
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
                    "allowed_capabilities": ["inspect_workload"],
                    "max_actions": 1,
                },
            }
        ],
    }
    if quality_gate is not None:
        payload["quality_gate"] = quality_gate
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_evaluation_dataset_file(root: Path) -> None:
    suite_path = root / "suites" / "healthy.json"
    suite_path.parent.mkdir(parents=True, exist_ok=True)
    write_evaluation_suite_file(suite_path)
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
                        "description": "Healthy workload regression suite",
                        "tags": ["smoke"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def write_domain_package_file(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "resources").mkdir(parents=True, exist_ok=True)
    (root / "resources" / "runbook.md").touch()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "apiVersion": "agent.nantian.dev/v1alpha1",
                "kind": "DomainPackage",
                "metadata": {
                    "name": "kubernetes",
                    "version": "0.2.0",
                    "description": "Kubernetes domain package",
                    "tags": ["kubernetes", "ops"],
                },
                "entrypoint": "kubernetes.domain:build_domain",
                "ontology": ["Deployment"],
                "capabilities": ["inspect_workload", "scale_workload"],
                "tools": ["kubernetes_inspect_workload"],
                "policies": ["kubernetes-scale-safety"],
                "procedures": ["diagnose_unhealthy_workload"],
                "knowledge": ["kubernetes readiness"],
                "evaluators": ["workload-health"],
                "context_providers": ["kubernetes_context"],
                "prompts": ["diagnostic_prompt"],
                "resources": ["resources/runbook.md"],
                "required_tools": ["kubernetes_api"],
                "compatibility": {
                    "runtime_api": ">=0.1,<1",
                    "domain_api": "agent.nantian.dev/v1alpha1",
                },
                "security": {"side_effects": "reversible"},
            }
        ),
        encoding="utf-8",
    )


def write_runtime_domain_package_file(
    root: Path,
    *,
    module_name: str,
    capability_name: str = "inspect_widget",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "resources").mkdir(parents=True, exist_ok=True)
    (root / "resources" / "runbook.md").write_text("Inspect the widget first.\n")
    (root / f"{module_name}.py").write_text(
        f"""
from __future__ import annotations

from universal_agent import BaseDomainRuntime, immutable_json
from universal_agent.core import (
    CapabilityCategory,
    CapabilityDefinition,
    DomainManifest,
    DomainMetadata,
    JsonMapping,
    ToolDefinition,
)
from universal_agent.evaluation import CriteriaEvaluator, Evaluator
from universal_agent.tools import Tool


class InspectWidgetTool:
    definition = ToolDefinition("inspect_widget", "Inspect widget state", ("{capability_name}",))

    async def execute(self, arguments: JsonMapping) -> JsonMapping:
        return immutable_json({{"healthy": True}})


class WidgetDomain(BaseDomainRuntime):
    manifest = DomainManifest(
        "agent.nantian.dev/v1alpha1",
        "Domain",
        DomainMetadata("widget", "1.0.0", "Widget domain"),
        ("Widget",),
        ("{capability_name}",),
        ("criteria",),
    )

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return (
            CapabilityDefinition(
                "{capability_name}",
                "Inspect widget health",
                CapabilityCategory.OBSERVATION,
            ),
        )

    def tools(self) -> tuple[Tool, ...]:
        return (InspectWidgetTool(),)

    def evaluators(self) -> tuple[Evaluator, ...]:
        return (CriteriaEvaluator(),)


def build_domain() -> WidgetDomain:
    return WidgetDomain()
""".lstrip(),
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "apiVersion": "agent.nantian.dev/v1alpha1",
                "kind": "DomainPackage",
                "metadata": {
                    "name": "widget",
                    "version": "1.0.0",
                    "description": "Widget domain package",
                    "tags": ["sdk"],
                },
                "entrypoint": f"{module_name}:build_domain",
                "ontology": ["Widget"],
                "capabilities": ["inspect_widget"],
                "tools": ["inspect_widget"],
                "evaluators": ["criteria"],
                "resources": ["resources/runbook.md"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def write_profile_config_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "name": "kubernetes-operator",
                "version": "1.0.0",
                "description": "Kubernetes operator profile",
                "domain": {"name": "kubernetes", "version": "0.2.0"},
                "runtime": {"domain": {"name": "kubernetes", "version": "0.2.0"}},
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_cli_init_writes_parseable_profile_config(tmp_path: Path) -> None:
    output = StringIO()
    profile_path = tmp_path / "profile.json"
    store_path = tmp_path / "store"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--profile",
            "production-operator",
            "--environment",
            "production",
            "--store-path",
            str(store_path),
        ],
        stdout=output,
    )
    payload = read_json(output)
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert payload == {
        "status": "created",
        "profile": "production-operator",
        "path": str(profile_path),
    }
    assert profile.name == "production-operator"
    assert profile.domain == DomainConfig("kubernetes", "0.2.0")
    assert profile.runtime.store == StoreConfig.file(str(store_path))
    assert profile.runtime.environment["environment"] == "production"


@pytest.mark.asyncio
async def test_cli_init_can_write_sqlite_profile_config(tmp_path: Path) -> None:
    output = StringIO()
    profile_path = tmp_path / "sqlite-profile.json"
    store_path = tmp_path / "runtime.sqlite3"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--store-backend",
            "sqlite",
            "--store-path",
            str(store_path),
        ],
        stdout=output,
    )
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert profile.runtime.store == StoreConfig.sqlite(str(store_path))


@pytest.mark.asyncio
async def test_cli_init_can_write_file_backed_distributed_queue_config(tmp_path: Path) -> None:
    output = StringIO()
    profile_path = tmp_path / "profile.json"
    store_path = tmp_path / "runtime-store"
    queue_path = tmp_path / "work-queue.json"
    locks_path = tmp_path / "distributed-locks.json"
    workers_path = tmp_path / "workers.json"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--store-path",
            str(store_path),
            "--distributed-queue-backend",
            "file",
            "--distributed-queue-path",
            str(queue_path),
            "--distributed-locks-backend",
            "file",
            "--distributed-locks-path",
            str(locks_path),
            "--distributed-workers-backend",
            "file",
            "--distributed-workers-path",
            str(workers_path),
            "--distributed-terminal-retention-seconds",
            "3600",
        ],
        stdout=output,
    )
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert profile.runtime.store == StoreConfig.file(str(store_path))
    assert profile.runtime.distributed_queue == StoreConfig.file(str(queue_path))
    assert profile.runtime.distributed_locks == StoreConfig.file(str(locks_path))
    assert profile.runtime.distributed_workers == StoreConfig.file(str(workers_path))
    assert profile.runtime.distributed_terminal_retention_seconds == 3600.0


@pytest.mark.asyncio
async def test_cli_init_can_write_sqlite_backed_distributed_locks_config(tmp_path: Path) -> None:
    output = StringIO()
    profile_path = tmp_path / "profile.json"
    locks_path = tmp_path / "distributed-locks.sqlite3"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--distributed-locks-backend",
            "sqlite",
            "--distributed-locks-path",
            str(locks_path),
        ],
        stdout=output,
    )
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert profile.runtime.distributed_locks == StoreConfig.sqlite(str(locks_path))


@pytest.mark.asyncio
async def test_cli_init_can_write_sqlite_backed_distributed_queue_config(tmp_path: Path) -> None:
    output = StringIO()
    profile_path = tmp_path / "profile.json"
    queue_path = tmp_path / "work-queue.sqlite3"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--distributed-queue-backend",
            "sqlite",
            "--distributed-queue-path",
            str(queue_path),
        ],
        stdout=output,
    )
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert profile.runtime.distributed_queue == StoreConfig.sqlite(str(queue_path))


@pytest.mark.asyncio
async def test_cli_init_can_write_sqlite_backed_distributed_workers_config(tmp_path: Path) -> None:
    output = StringIO()
    profile_path = tmp_path / "profile.json"
    workers_path = tmp_path / "workers.sqlite3"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--distributed-workers-backend",
            "sqlite",
            "--distributed-workers-path",
            str(workers_path),
        ],
        stdout=output,
    )
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert profile.runtime.distributed_workers == StoreConfig.sqlite(str(workers_path))


@pytest.mark.asyncio
async def test_cli_init_can_write_memory_profile_config(tmp_path: Path) -> None:
    output = StringIO()
    profile_path = tmp_path / "memory-profile.json"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--store-backend",
            "memory",
        ],
        stdout=output,
    )
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert profile.runtime.store == StoreConfig.memory()


@pytest.mark.asyncio
async def test_cli_init_can_write_kubectl_domain_backend_config(tmp_path: Path) -> None:
    output = StringIO()
    profile_path = tmp_path / "kubectl-profile.json"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--domain-backend",
            "kubectl",
            "--kubectl-namespace",
            "prod",
            "--kubectl-context",
            "prod-cluster",
            "--kubectl-kubeconfig",
            "/tmp/kubeconfig",
            "--kubectl-timeout-seconds",
            "4.5",
        ],
        stdout=output,
    )
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert profile.domain.backend == "kubectl"
    assert profile.runtime.domain.backend == "kubectl"
    assert profile.runtime.domain.settings == {
        "default_namespace": "prod",
        "context": "prod-cluster",
        "kubeconfig": "/tmp/kubeconfig",
        "timeout_seconds": 4.5,
    }


@pytest.mark.asyncio
async def test_cli_init_can_write_kubernetes_api_domain_backend_config(tmp_path: Path) -> None:
    output = StringIO()
    profile_path = tmp_path / "kubernetes-api-profile.json"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--domain-backend",
            "kubernetes_api",
            "--kubernetes-api-server",
            "https://cluster.example.test",
            "--kubernetes-api-namespace",
            "prod",
            "--kubernetes-api-token-env",
            "KUBERNETES_API_TOKEN",
            "--kubernetes-api-token-secret",
            "prod_kubernetes_api_token",
            "--kubernetes-api-timeout-seconds",
            "4.5",
        ],
        stdout=output,
    )
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert profile.domain.backend == "kubernetes_api"
    assert profile.runtime.domain.backend == "kubernetes_api"
    assert profile.runtime.domain.settings == {
        "api_server": "https://cluster.example.test",
        "default_namespace": "prod",
        "bearer_token_secret": "prod_kubernetes_api_token",
        "timeout_seconds": 4.5,
    }
    assert profile.runtime.secrets == (
        SecretRef.env("prod_kubernetes_api_token", "KUBERNETES_API_TOKEN"),
    )


@pytest.mark.asyncio
async def test_cli_init_can_write_kubernetes_api_file_secret_config(tmp_path: Path) -> None:
    output = StringIO()
    token_path = tmp_path / "kubernetes-token"
    profile_path = tmp_path / "kubernetes-api-file-profile.json"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--domain-backend",
            "kubernetes_api",
            "--kubernetes-api-server",
            "https://cluster.example.test",
            "--kubernetes-api-token-file",
            str(token_path),
            "--kubernetes-api-token-secret",
            "prod_kubernetes_api_token",
        ],
        stdout=output,
    )
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert profile.runtime.domain.settings["bearer_token_secret"] == "prod_kubernetes_api_token"
    assert profile.runtime.secrets == (
        SecretRef.file("prod_kubernetes_api_token", str(token_path)),
    )


@pytest.mark.asyncio
async def test_cli_init_rejects_kubernetes_api_backend_without_server(
    tmp_path: Path,
) -> None:
    output = StringIO()
    error = StringIO()
    profile_path = tmp_path / "kubernetes-api-profile.json"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--domain-backend",
            "kubernetes_api",
        ],
        stdout=output,
        stderr=error,
    )

    assert status == 2
    assert output.getvalue() == ""
    assert "kubernetes_api backend requires --kubernetes-api-server" in error.getvalue()
    assert not profile_path.exists()


@pytest.mark.asyncio
async def test_cli_init_can_write_json_http_model_config(tmp_path: Path) -> None:
    output = StringIO()
    profile_path = tmp_path / "json-http-profile.json"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--model-provider",
            "json_http",
            "--model-name",
            "runtime-decider",
            "--model-endpoint",
            "https://models.example.test/decide",
            "--model-api-key-env",
            "RUNTIME_MODEL_API_KEY",
            "--model-api-key-secret",
            "runtime_model_api_key",
            "--model-timeout-seconds",
            "4.5",
            "--model-header",
            "X-Agent-Runtime=test",
        ],
        stdout=output,
    )
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert profile.runtime.secrets == (
        SecretRef.env("runtime_model_api_key", "RUNTIME_MODEL_API_KEY"),
    )
    assert profile.runtime.model == ModelConfig.json_http(
        name="runtime-decider",
        endpoint="https://models.example.test/decide",
        api_key_secret="runtime_model_api_key",
        timeout_seconds=4.5,
        headers={"X-Agent-Runtime": "test"},
    )


@pytest.mark.asyncio
async def test_cli_init_can_write_json_http_model_file_secret_config(tmp_path: Path) -> None:
    output = StringIO()
    secret_path = tmp_path / "model-api-key"
    profile_path = tmp_path / "json-http-file-profile.json"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--model-provider",
            "json_http",
            "--model-name",
            "runtime-decider",
            "--model-endpoint",
            "https://models.example.test/decide",
            "--model-api-key-file",
            str(secret_path),
            "--model-api-key-secret",
            "runtime_model_api_key",
        ],
        stdout=output,
    )
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert profile.runtime.secrets == (SecretRef.file("runtime_model_api_key", str(secret_path)),)
    assert profile.runtime.model.api_key_secret == "runtime_model_api_key"


@pytest.mark.asyncio
async def test_cli_init_can_write_openai_responses_model_config(tmp_path: Path) -> None:
    output = StringIO()
    profile_path = tmp_path / "openai-profile.json"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--model-provider",
            "openai_responses",
            "--model-name",
            "gpt-runtime",
            "--model-api-key-env",
            "OPENAI_API_KEY",
            "--model-api-key-secret",
            "openai_api_key",
            "--model-timeout-seconds",
            "4.5",
            "--model-header",
            "OpenAI-Organization=org-test",
        ],
        stdout=output,
    )
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert profile.runtime.secrets == (SecretRef.env("openai_api_key", "OPENAI_API_KEY"),)
    assert profile.runtime.model == ModelConfig.openai_responses(
        name="gpt-runtime",
        api_key_secret="openai_api_key",
        timeout_seconds=4.5,
        headers={"OpenAI-Organization": "org-test"},
    )


@pytest.mark.asyncio
async def test_cli_init_openai_responses_requires_model_name(tmp_path: Path) -> None:
    output = StringIO()
    error = StringIO()
    profile_path = tmp_path / "openai-profile.json"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--model-provider",
            "openai_responses",
            "--model-api-key-env",
            "OPENAI_API_KEY",
        ],
        stdout=output,
        stderr=error,
    )

    assert status == 2
    assert output.getvalue() == ""
    assert "openai_responses model requires --model-name" in error.getvalue()
    assert not profile_path.exists()


@pytest.mark.asyncio
async def test_cli_init_openai_responses_requires_api_key_secret(tmp_path: Path) -> None:
    output = StringIO()
    error = StringIO()
    profile_path = tmp_path / "openai-profile.json"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--model-provider",
            "openai_responses",
            "--model-name",
            "gpt-runtime",
        ],
        stdout=output,
        stderr=error,
    )

    assert status == 2
    assert output.getvalue() == ""
    assert "openai_responses model requires model API key secret" in error.getvalue()
    assert not profile_path.exists()


@pytest.mark.asyncio
async def test_cli_init_rejects_secret_env_and_file_together(tmp_path: Path) -> None:
    output = StringIO()
    error = StringIO()
    profile_path = tmp_path / "json-http-profile.json"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--model-provider",
            "json_http",
            "--model-name",
            "runtime-decider",
            "--model-endpoint",
            "https://models.example.test/decide",
            "--model-api-key-env",
            "MODEL_API_KEY",
            "--model-api-key-file",
            str(tmp_path / "model-api-key"),
        ],
        stdout=output,
        stderr=error,
    )

    assert status == 2
    assert output.getvalue() == ""
    assert "--model-api-key accepts either env or file" in error.getvalue()
    assert not profile_path.exists()


@pytest.mark.asyncio
async def test_cli_init_rejects_invalid_model_header(tmp_path: Path) -> None:
    output = StringIO()
    error = StringIO()
    profile_path = tmp_path / "json-http-profile.json"

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--model-provider",
            "json_http",
            "--model-name",
            "runtime-decider",
            "--model-endpoint",
            "https://models.example.test/decide",
            "--model-header",
            "X-Agent-Runtime",
        ],
        stdout=output,
        stderr=error,
    )

    assert status == 2
    assert output.getvalue() == ""
    assert "model-header must be KEY=VALUE" in error.getvalue()
    assert not profile_path.exists()


@pytest.mark.asyncio
async def test_cli_init_rejects_existing_profile_without_force(tmp_path: Path) -> None:
    output = StringIO()
    error = StringIO()
    profile_path = tmp_path / "profile.json"
    profile_path.write_text("{}", encoding="utf-8")

    status = await run_cli(
        ["init", "--output", str(profile_path)],
        stdout=output,
        stderr=error,
    )

    assert status == 2
    assert output.getvalue() == ""
    assert f"profile config already exists: {profile_path}" in error.getvalue()


@pytest.mark.asyncio
async def test_cli_init_force_overwrites_existing_profile(tmp_path: Path) -> None:
    output = StringIO()
    profile_path = tmp_path / "profile.json"
    profile_path.write_text("{}", encoding="utf-8")

    status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--profile",
            "replacement-profile",
            "--environment",
            "staging",
            "--force",
        ],
        stdout=output,
    )
    profile = ProfileConfig.from_json_file(profile_path).to_profile()

    assert status == 0
    assert profile.name == "replacement-profile"
    assert profile.runtime.environment["environment"] == "staging"


@pytest.mark.asyncio
async def test_cli_profile_config_drives_run_and_persisted_session_reads(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.json"
    store_path = tmp_path / "runtime-store"
    init_output = StringIO()
    run_output = StringIO()
    list_output = StringIO()
    config_output = StringIO()

    init_status = await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--profile",
            "configured-operator",
            "--environment",
            "production",
            "--store-backend",
            "file",
            "--store-path",
            str(store_path),
        ],
        stdout=init_output,
    )
    run_status = await run_cli(
        [
            "--profile-config",
            str(profile_path),
            "run",
            "configured-operator",
            "Verify configured workload health",
        ],
        stdout=run_output,
    )
    run_payload = read_json(run_output)
    result = run_payload["result"]
    assert isinstance(result, dict)
    session_id = result["session_id"]
    assert isinstance(session_id, str)

    list_status = await run_cli(
        ["--profile-config", str(profile_path), "session", "list"],
        stdout=list_output,
    )
    config_status = await run_cli(
        ["--profile-config", str(profile_path), "config", "show"],
        stdout=config_output,
    )
    list_payload = read_json(list_output)
    config_payload = read_json(config_output)

    assert init_status == 0
    assert run_status == 0
    assert list_status == 0
    assert config_status == 0
    assert result["status"] == "completed"
    assert run_payload["session"]["goal_description"] == "Verify configured workload health"
    assert list_payload["sessions"][0]["session_id"] == session_id
    assert config_payload["environment"] == {"environment": "production"}
    assert config_payload["store"] == {"backend": "file", "path": str(store_path)}
    assert config_payload["distributed_queue"] == {"backend": "memory", "path": None}
    assert config_payload["distributed_locks"] == {"backend": "memory", "path": None}
    assert config_payload["distributed_workers"] == {"backend": "memory", "path": None}
    assert config_payload["distributed_terminal_retention_seconds"] is None
    assert list((store_path / "sessions").glob("*.json"))
    assert (store_path / "events.jsonl").exists()


@pytest.mark.asyncio
async def test_cli_config_show_exposes_kubectl_domain_backend_config(tmp_path: Path) -> None:
    profile_path = tmp_path / "kubectl-profile.json"
    init_output = StringIO()
    config_output = StringIO()

    await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--domain-backend",
            "kubectl",
            "--kubectl-namespace",
            "prod",
            "--kubectl-context",
            "prod-cluster",
        ],
        stdout=init_output,
    )
    status = await run_cli(
        ["--profile-config", str(profile_path), "config", "show"],
        stdout=config_output,
    )
    config_payload = read_json(config_output)

    assert status == 0
    assert config_payload["domains"] == [
        {
            "name": "kubernetes",
            "version": "0.2.0",
            "primary": True,
            "backend": "kubectl",
            "settings": {
                "default_namespace": "prod",
                "context": "prod-cluster",
                "timeout_seconds": 10.0,
            },
        }
    ]


@pytest.mark.asyncio
async def test_cli_config_show_exposes_kubernetes_api_backend_without_secret_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KUBERNETES_API_TOKEN", "secret-token")
    profile_path = tmp_path / "kubernetes-api-profile.json"
    init_output = StringIO()
    config_output = StringIO()

    await run_cli(
        [
            "init",
            "--output",
            str(profile_path),
            "--domain-backend",
            "kubernetes_api",
            "--kubernetes-api-server",
            "https://cluster.example.test",
            "--kubernetes-api-namespace",
            "prod",
            "--kubernetes-api-token-env",
            "KUBERNETES_API_TOKEN",
        ],
        stdout=init_output,
    )
    status = await run_cli(
        ["--profile-config", str(profile_path), "config", "show"],
        stdout=config_output,
    )
    config_payload = read_json(config_output)

    assert status == 0
    assert config_payload["domains"] == [
        {
            "name": "kubernetes",
            "version": "0.2.0",
            "primary": True,
            "backend": "kubernetes_api",
            "settings": {
                "api_server": "https://cluster.example.test",
                "default_namespace": "prod",
                "bearer_token_secret": "<redacted>",
                "timeout_seconds": 10.0,
            },
        }
    ]
    assert config_payload["secrets"] == [
        {
            "name": "kubernetes_api_token",
            "source": "env",
            "key": "KUBERNETES_API_TOKEN",
            "required": True,
            "available": True,
            "status": "available",
        }
    ]
    assert "secret-token" not in config_output.getvalue()


@pytest.mark.asyncio
async def test_cli_config_show_resolves_kubernetes_api_file_secret_without_values(
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "kubernetes-token"
    token_path.write_text("file-secret-token\n", encoding="utf-8")
    profile_path = tmp_path / "kubernetes-api-file-profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "name": "production-operator",
                "version": "1.0.0",
                "domain": {"name": "kubernetes", "version": "0.2.0"},
                "runtime": {
                    "secrets": {
                        "kubernetes_api_token": {
                            "source": "file",
                            "key": str(token_path),
                            "required": True,
                        }
                    },
                    "domain": {
                        "name": "kubernetes",
                        "version": "0.2.0",
                        "backend": "kubernetes_api",
                        "settings": {
                            "api_server": "https://cluster.example.test",
                            "default_namespace": "prod",
                            "bearer_token_secret": "kubernetes_api_token",
                        },
                    },
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    output = StringIO()

    status = await run_cli(
        ["--profile-config", str(profile_path), "config", "show"],
        stdout=output,
    )
    payload = read_json(output)

    assert status == 0
    assert payload["secrets"] == [
        {
            "name": "kubernetes_api_token",
            "source": "file",
            "key": str(token_path),
            "required": True,
            "available": True,
            "status": "available",
        }
    ]
    assert payload["domains"][0]["settings"]["bearer_token_secret"] == "<redacted>"
    assert "file-secret-token" not in output.getvalue()


@pytest.mark.asyncio
async def test_cli_run_submits_goal_through_service() -> None:
    service, backend = build_cli_service([inspect_workload(), finish()])
    output = StringIO()

    status = await run_cli(
        ["run", "production-operator", "Verify workload health"],
        service=service,
        stdout=output,
    )
    payload = read_json(output)

    assert status == 0
    assert payload["result"]["status"] == "completed"
    assert payload["session"]["goal_description"] == "Verify workload health"
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_run_accepts_custom_success_criteria() -> None:
    service, backend = build_cli_service([inspect_workload(), finish()])
    output = StringIO()

    status = await run_cli(
        [
            "run",
            "production-operator",
            "Verify workload resource identity",
            "--success",
            'resource="deployment/example"',
        ],
        service=service,
        stdout=output,
    )
    payload = read_json(output)
    session = payload["session"]

    assert status == 0
    assert payload["result"]["status"] == "completed"
    assert session["satisfied_criteria"]["resource"] == "deployment/example"
    assert session["tasks"][0]["required_criteria"] == ["resource"]
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_tui_renders_runtime_service_snapshot() -> None:
    service, backend = build_cli_service([inspect_workload(), finish()])
    run_output = StringIO()
    tui_output = StringIO()

    run_status = await run_cli(
        ["run", "production-operator", "Verify workload health"],
        service=service,
        stdout=run_output,
    )
    run_payload = read_json(run_output)
    session_id = run_payload["result"]["session_id"]
    assert isinstance(session_id, str)

    tui_status = await run_cli(
        ["tui", "--session-id", session_id, "--event-limit", "20"],
        service=service,
        stdout=tui_output,
    )
    rendered = tui_output.getvalue()

    assert run_status == 0
    assert tui_status == 0
    assert "Universal Agent Runtime TUI" in rendered
    assert "Health: ok | Ready: yes" in rendered
    assert "Active Domains" in rendered
    assert "kubernetes@0.2.0" in rendered
    assert "Selected Session" in rendered
    assert "Verify workload health" in rendered
    assert "Recent Events" in rendered
    assert "ActionStarted" in rendered
    assert "capability=inspect_workload" in rendered
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_ecosystem_catalog_indexes_local_artifacts(tmp_path: Path) -> None:
    service, _ = build_cli_service([])
    domain_root = tmp_path / "domains"
    dataset_root = tmp_path / "datasets"
    profile_root = tmp_path / "profiles"
    write_domain_package_file(domain_root / "kubernetes")
    write_evaluation_dataset_file(dataset_root / "kubernetes")
    write_profile_config_file(profile_root / "kubernetes.profile.json")
    output = StringIO()

    status = await run_cli(
        [
            "ecosystem",
            "catalog",
            "--domain-package-dir",
            str(domain_root),
            "--dataset-dir",
            str(dataset_root),
            "--profile-dir",
            str(profile_root),
        ],
        service=service,
        stdout=output,
    )
    payload = read_json(output)

    assert status == 0
    assert payload["summary"] == {
        "domain_package_count": 1,
        "evaluation_dataset_count": 1,
        "profile_count": 1,
        "total_items": 3,
    }
    assert payload["domain_packages"][0]["name"] == "kubernetes"
    assert payload["domain_packages"][0]["entrypoint"] == "kubernetes.domain:build_domain"
    assert payload["domain_packages"][0]["ontology"] == ["Deployment"]
    assert payload["domain_packages"][0]["capability_names"] == [
        "inspect_workload",
        "scale_workload",
    ]
    assert payload["domain_packages"][0]["tool_names"] == ["kubernetes_inspect_workload"]
    assert payload["domain_packages"][0]["policy_names"] == ["kubernetes-scale-safety"]
    assert payload["domain_packages"][0]["procedure_names"] == ["diagnose_unhealthy_workload"]
    assert payload["domain_packages"][0]["knowledge_names"] == ["kubernetes readiness"]
    assert payload["domain_packages"][0]["evaluator_names"] == ["workload-health"]
    assert payload["domain_packages"][0]["context_provider_names"] == ["kubernetes_context"]
    assert payload["domain_packages"][0]["prompt_names"] == ["diagnostic_prompt"]
    assert payload["domain_packages"][0]["resource_names"] == ["resources/runbook.md"]
    assert payload["domain_packages"][0]["compatibility"] == {
        "runtime_api": ">=0.1,<1",
        "domain_api": "agent.nantian.dev/v1alpha1",
    }
    assert payload["domain_packages"][0]["security"] == {"side_effects": "reversible"}
    assert payload["evaluation_datasets"][0]["name"] == "kubernetes-remediation"
    assert payload["profiles"][0]["name"] == "kubernetes-operator"


@pytest.mark.asyncio
async def test_cli_ecosystem_verify_reports_reference_integrity(tmp_path: Path) -> None:
    service, _ = build_cli_service([])
    domain_root = tmp_path / "domains"
    dataset_root = tmp_path / "datasets"
    profile_root = tmp_path / "profiles"
    write_domain_package_file(domain_root / "kubernetes")
    write_evaluation_dataset_file(dataset_root / "kubernetes")
    write_profile_config_file(profile_root / "kubernetes.profile.json")
    passing_output = StringIO()
    failing_output = StringIO()

    passing_status = await run_cli(
        [
            "ecosystem",
            "verify",
            "--domain-package-dir",
            str(domain_root),
            "--dataset-dir",
            str(dataset_root),
            "--profile-dir",
            str(profile_root),
        ],
        service=service,
        stdout=passing_output,
    )
    failing_status = await run_cli(
        [
            "ecosystem",
            "verify",
            "--dataset-dir",
            str(dataset_root),
            "--profile-dir",
            str(profile_root),
        ],
        service=service,
        stdout=failing_output,
    )
    passing = read_json(passing_output)
    failing = read_json(failing_output)
    failed_checks = {item["name"]: item for item in failing["checks"]}

    assert passing_status == 0
    assert failing_status == 0
    assert passing["passed"] is True
    assert passing["failed_check_count"] == 0
    assert failing["passed"] is False
    assert failing["failed_check_count"] == 2
    assert failed_checks["profile_domains_registered"]["passed"] is False
    assert failed_checks["dataset_domains_registered"]["passed"] is False


@pytest.mark.asyncio
async def test_cli_ecosystem_export_writes_registry_manifest(tmp_path: Path) -> None:
    service, _ = build_cli_service([])
    domain_root = tmp_path / "domains"
    dataset_root = tmp_path / "datasets"
    profile_root = tmp_path / "profiles"
    output_path = tmp_path / "registry" / "ecosystem.json"
    write_domain_package_file(domain_root / "kubernetes")
    write_evaluation_dataset_file(dataset_root / "kubernetes")
    write_profile_config_file(profile_root / "kubernetes.profile.json")
    inline_output = StringIO()
    write_output = StringIO()
    registry_output = StringIO()
    registry_verify_output = StringIO()
    install_output = StringIO()
    install_plan_output = StringIO()
    duplicate_output = StringIO()
    duplicate_error = StringIO()
    force_output = StringIO()

    inline_status = await run_cli(
        [
            "ecosystem",
            "export",
            "--domain-package-dir",
            str(domain_root),
            "--dataset-dir",
            str(dataset_root),
            "--profile-dir",
            str(profile_root),
            "--name",
            "ops-ecosystem",
            "--version",
            "1.2.3",
        ],
        service=service,
        stdout=inline_output,
    )
    write_status = await run_cli(
        [
            "ecosystem",
            "export",
            "--domain-package-dir",
            str(domain_root),
            "--dataset-dir",
            str(dataset_root),
            "--profile-dir",
            str(profile_root),
            "--output",
            str(output_path),
        ],
        service=service,
        stdout=write_output,
    )
    registry_status = await run_cli(
        ["ecosystem", "registry", str(output_path)],
        service=service,
        stdout=registry_output,
    )
    registry_verify_status = await run_cli(
        ["ecosystem", "registry", str(output_path), "--verify"],
        service=service,
        stdout=registry_verify_output,
    )
    install_status = await run_cli(
        ["ecosystem", "install", str(output_path)],
        service=service,
        stdout=install_output,
    )
    install_plan_status = await run_cli(
        ["ecosystem", "install", str(output_path), "--plan-only"],
        service=service,
        stdout=install_plan_output,
    )
    duplicate_status = await run_cli(
        [
            "ecosystem",
            "export",
            "--domain-package-dir",
            str(domain_root),
            "--output",
            str(output_path),
        ],
        service=service,
        stdout=duplicate_output,
        stderr=duplicate_error,
    )
    force_status = await run_cli(
        [
            "ecosystem",
            "export",
            "--domain-package-dir",
            str(domain_root),
            "--output",
            str(output_path),
            "--force",
        ],
        service=service,
        stdout=force_output,
    )
    inline = read_json(inline_output)
    written = read_json(write_output)
    registry = read_json(registry_output)
    registry_verify = read_json(registry_verify_output)
    install = read_json(install_output)
    install_plan = read_json(install_plan_output)
    forced = read_json(force_output)
    loaded = load_ecosystem_registry_manifest(output_path)

    assert inline_status == 0
    assert write_status == 0
    assert registry_status == 0
    assert registry_verify_status == 0
    assert install_status == 0
    assert install_plan_status == 0
    assert duplicate_status == 2
    assert force_status == 0
    assert inline["kind"] == "EcosystemRegistry"
    assert inline["metadata"] == {
        "name": "ops-ecosystem",
        "version": "1.2.3",
        "description": "Local Universal Agent ecosystem registry",
    }
    assert written["status"] == "created"
    assert written["path"] == str(output_path)
    assert written["manifest"]["summary"]["total_items"] == 3
    assert registry["kind"] == "EcosystemRegistry"
    assert registry["summary"]["total_items"] == 3
    assert registry_verify["passed"] is True
    assert registry_verify["failed_check_count"] == 0
    assert install["status"] == "installed"
    assert install["registry_count"] == 1
    assert install["domain_package_count"] == 1
    assert install["evaluation_dataset_count"] == 1
    assert install["profile_count"] == 1
    assert install["domain_package_registry_count"] == 1
    assert install["evaluation_dataset_registry_count"] == 1
    assert install["profile_registry_count"] == 1
    assert install["domain_packages"][0]["name"] == "kubernetes"
    assert install["domain_packages"][0]["resource_names"] == ["resources/runbook.md"]
    assert install["evaluation_datasets"][0]["name"] == "kubernetes-remediation"
    assert install["profiles"][0]["name"] == "kubernetes-operator"
    assert install_plan["status"] == "planned"
    assert install_plan["domain_package_count"] == 1
    assert install_plan["evaluation_dataset_count"] == 1
    assert install_plan["profile_count"] == 1
    assert install_plan["domain_packages"][0]["name"] == "kubernetes"
    assert install_plan["domain_packages"][0]["entrypoint"] == "kubernetes.domain:build_domain"
    assert install_plan["evaluation_datasets"][0]["name"] == "kubernetes-remediation"
    assert install_plan["profiles"][0]["name"] == "kubernetes-operator"
    assert duplicate_output.getvalue() == ""
    assert "ecosystem registry manifest already exists" in duplicate_error.getvalue()
    assert forced["status"] == "updated"
    assert loaded.kind == "EcosystemRegistry"
    assert loaded.summary.domain_package_count == 1


@pytest.mark.asyncio
async def test_cli_ecosystem_install_requires_explicit_unverified_signature_trust(
    tmp_path: Path,
) -> None:
    service, _ = build_cli_service([])
    domain_root = tmp_path / "domains"
    output_path = tmp_path / "registry" / "ecosystem.json"
    write_domain_package_file(domain_root / "kubernetes")
    export_output = StringIO()
    rejected_output = StringIO()
    rejected_error = StringIO()
    allowed_plan_output = StringIO()
    allowed_install_output = StringIO()

    export_status = await run_cli(
        [
            "ecosystem",
            "export",
            "--domain-package-dir",
            str(domain_root),
            "--output",
            str(output_path),
        ],
        service=service,
        stdout=export_output,
    )
    signed_payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert isinstance(signed_payload, dict)
    signed_payload["metadata"] = {
        "name": "local-ecosystem",
        "version": "0.1.0",
        "description": "Local Universal Agent ecosystem registry",
        "signature": {
            "algorithm": "ed25519",
            "value": "local-test-signature",
        },
    }
    output_path.write_text(json.dumps(signed_payload), encoding="utf-8")

    rejected_status = await run_cli(
        ["ecosystem", "install", str(output_path), "--plan-only"],
        service=service,
        stdout=rejected_output,
        stderr=rejected_error,
    )
    allowed_plan_status = await run_cli(
        [
            "ecosystem",
            "install",
            str(output_path),
            "--plan-only",
            "--allow-unverified-signatures",
        ],
        service=service,
        stdout=allowed_plan_output,
    )
    allowed_install_status = await run_cli(
        ["ecosystem", "install", str(output_path), "--allow-unverified-signatures"],
        service=service,
        stdout=allowed_install_output,
    )
    allowed_plan = read_json(allowed_plan_output)
    allowed_install = read_json(allowed_install_output)

    assert export_status == 0
    assert rejected_status == 2
    assert rejected_output.getvalue() == ""
    assert "pass a signature verifier" in rejected_error.getvalue()
    assert allowed_plan_status == 0
    assert allowed_plan["status"] == "planned"
    assert allowed_plan["domain_package_count"] == 1
    assert allowed_install_status == 0
    assert allowed_install["status"] == "installed"
    assert allowed_install["domain_package_count"] == 1


@pytest.mark.asyncio
async def test_cli_ecosystem_store_manages_file_backed_registry_manifests(
    tmp_path: Path,
) -> None:
    service, _ = build_cli_service([])
    domain_root = tmp_path / "domains"
    manifest_path = tmp_path / "ecosystem.json"
    store_dir = tmp_path / "registry-store"
    write_domain_package_file(domain_root / "kubernetes")
    export_output = StringIO()
    save_output = StringIO()
    list_output = StringIO()
    show_output = StringIO()
    verify_output = StringIO()
    duplicate_output = StringIO()
    duplicate_error = StringIO()
    force_output = StringIO()

    export_status = await run_cli(
        [
            "ecosystem",
            "export",
            "--domain-package-dir",
            str(domain_root),
            "--name",
            "ops-ecosystem",
            "--version",
            "1.0.0",
            "--output",
            str(manifest_path),
        ],
        service=service,
        stdout=export_output,
    )
    save_status = await run_cli(
        ["ecosystem", "store", "save", str(manifest_path), "--store-dir", str(store_dir)],
        service=service,
        stdout=save_output,
    )
    list_status = await run_cli(
        ["ecosystem", "store", "list", "--store-dir", str(store_dir)],
        service=service,
        stdout=list_output,
    )
    show_status = await run_cli(
        [
            "ecosystem",
            "store",
            "show",
            "ops-ecosystem",
            "1.0.0",
            "--store-dir",
            str(store_dir),
        ],
        service=service,
        stdout=show_output,
    )
    verify_status = await run_cli(
        [
            "ecosystem",
            "store",
            "show",
            "ops-ecosystem",
            "1.0.0",
            "--store-dir",
            str(store_dir),
            "--verify",
        ],
        service=service,
        stdout=verify_output,
    )
    duplicate_status = await run_cli(
        ["ecosystem", "store", "save", str(manifest_path), "--store-dir", str(store_dir)],
        service=service,
        stdout=duplicate_output,
        stderr=duplicate_error,
    )
    force_status = await run_cli(
        [
            "ecosystem",
            "store",
            "save",
            str(manifest_path),
            "--store-dir",
            str(store_dir),
            "--force",
        ],
        service=service,
        stdout=force_output,
    )
    saved = read_json(save_output)
    listed = read_json(list_output)
    shown = read_json(show_output)
    verified = read_json(verify_output)
    forced = read_json(force_output)

    assert export_status == 0
    assert save_status == 0
    assert list_status == 0
    assert show_status == 0
    assert verify_status == 0
    assert duplicate_status == 2
    assert force_status == 0
    assert saved["status"] == "created"
    assert saved["manifest"]["name"] == "ops-ecosystem"
    assert listed["registry_count"] == 1
    assert listed["registries"][0]["version"] == "1.0.0"
    assert shown["kind"] == "EcosystemRegistry"
    assert shown["metadata"]["name"] == "ops-ecosystem"
    assert verified["passed"] is True
    assert duplicate_output.getvalue() == ""
    assert "ecosystem registry manifest already exists" in duplicate_error.getvalue()
    assert forced["status"] == "updated"


@pytest.mark.asyncio
async def test_cli_session_diagnostics_renders_evidence_and_world_facts() -> None:
    service, backend = build_cli_service([inspect_workload(), finish()])
    run_output = StringIO()
    diagnostics_output = StringIO()
    evidence_output = StringIO()
    world_output = StringIO()
    neighborhood_output = StringIO()

    run_status = await run_cli(
        ["run", "production-operator", "Verify workload health"],
        service=service,
        stdout=run_output,
    )
    run_payload = read_json(run_output)
    session_id = run_payload["result"]["session_id"]
    assert isinstance(session_id, str)

    diagnostics_status = await run_cli(
        ["session", "diagnostics", session_id],
        service=service,
        stdout=diagnostics_output,
    )
    evidence_status = await run_cli(
        ["session", "evidence", session_id],
        service=service,
        stdout=evidence_output,
    )
    world_status = await run_cli(
        ["session", "world", session_id],
        service=service,
        stdout=world_output,
    )
    neighborhood_status = await run_cli(
        [
            "session",
            "world",
            session_id,
            "--entity",
            "deployment/example",
            "--relation",
            "owns",
        ],
        service=service,
        stdout=neighborhood_output,
    )
    diagnostics = read_json(diagnostics_output)
    evidence = read_json(evidence_output)
    world = read_json(world_output)
    neighborhood = read_json(neighborhood_output)
    evidence_claims = {item["claim"]: item for item in diagnostics["evidence"]}
    world_claims = {item["claim"]: item for item in diagnostics["world_facts"]}

    assert run_status == 0
    assert diagnostics_status == 0
    assert evidence_status == 0
    assert world_status == 0
    assert neighborhood_status == 0
    assert diagnostics["session"]["session_id"] == session_id
    assert evidence["session_id"] == session_id
    assert world["session_id"] == session_id
    assert evidence["evidence"] == diagnostics["evidence"]
    assert world["world_facts"] == diagnostics["world_facts"]
    history_items = world["world_fact_histories"]
    healthy_history = next(item for item in history_items if item["claim"] == "healthy")
    assert healthy_history["conflicting"] is False
    assert healthy_history["current"]["value"] is True
    assert len(healthy_history["candidates"]) == 1
    assert world["world_entities"] == diagnostics["world_entities"]
    assert world["world_relations"] == diagnostics["world_relations"]
    assert evidence_claims["healthy"]["value"] is True
    assert world_claims["healthy"]["value"] is True
    assert diagnostics["world_entities"][0]["entity_id"] == "deployment/example"
    assert diagnostics["world_entities"][0]["kind"] == "Deployment"
    assert diagnostics["world_entities"][0]["attributes"]["healthy"] is True
    assert diagnostics["world_relations"][0]["source"] == "deployment/example"
    assert diagnostics["world_relations"][0]["relation"] == "owns"
    assert diagnostics["world_relations"][0]["target"] == "pod/example-1"
    assert neighborhood["neighborhood"]["root"]["entity_id"] == "deployment/example"
    assert neighborhood["neighborhood"]["outgoing_relations"][0]["relation"] == "owns"
    assert neighborhood["neighborhood"]["outgoing_relations"][0]["target"] == "pod/example-1"
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_run_rejects_unknown_profile() -> None:
    service, _ = build_cli_service([])
    output = StringIO()
    error = StringIO()

    status = await run_cli(
        ["run", "missing-profile", "Verify workload health"],
        service=service,
        stdout=output,
        stderr=error,
    )

    assert status == 2
    assert output.getvalue() == ""
    assert "unknown profile: missing-profile" in error.getvalue()


@pytest.mark.asyncio
async def test_cli_run_rejects_invalid_success_criterion_json() -> None:
    service, _ = build_cli_service([])
    output = StringIO()
    error = StringIO()

    status = await run_cli(
        [
            "run",
            "production-operator",
            "Verify workload health",
            "--success",
            "healthy=yes",
        ],
        service=service,
        stdout=output,
        stderr=error,
    )

    assert status == 2
    assert output.getvalue() == ""
    assert "success criterion healthy must be valid JSON" in error.getvalue()


@pytest.mark.asyncio
async def test_cli_profile_show_exposes_one_profile() -> None:
    service, _ = build_cli_service([])
    output = StringIO()

    status = await run_cli(
        ["profile", "show", "production-operator"],
        service=service,
        stdout=output,
    )
    payload = read_json(output)

    assert status == 0
    assert payload == {
        "name": "production-operator",
        "version": "1.0.0",
        "description": "Production Kubernetes operator",
        "domain_name": "kubernetes",
        "domain_version": "0.2.0",
        "domains": [{"name": "kubernetes", "version": "0.2.0"}],
    }


@pytest.mark.asyncio
async def test_cli_profile_verify_checks_profile_config_catalog(tmp_path: Path) -> None:
    service, _ = build_cli_service([])
    profile_dir = tmp_path / "profiles"
    write_profile_config_file(profile_dir / "kubernetes.profile.json")
    output = StringIO()

    status = await run_cli(
        ["profile", "verify", "--profile-dir", str(profile_dir)],
        service=service,
        stdout=output,
    )
    payload = read_json(output)

    assert status == 0
    assert payload["passed"] is True
    assert payload["failed_check_count"] == 0
    assert {check["name"] for check in payload["checks"]} == {
        "profile_config_exists",
        "profile_config_matches_identity",
    }


@pytest.mark.asyncio
async def test_cli_profile_show_rejects_unknown_profile() -> None:
    service, _ = build_cli_service([])
    output = StringIO()
    error = StringIO()

    status = await run_cli(
        ["profile", "show", "missing-profile"],
        service=service,
        stdout=output,
        stderr=error,
    )

    assert status == 2
    assert output.getvalue() == ""
    assert "unknown profile: missing-profile" in error.getvalue()


@pytest.mark.asyncio
async def test_cli_repair_state_events_requires_confirmation_and_reports_clean() -> None:
    service, _ = build_cli_service([])
    output = StringIO()
    rejected_error = StringIO()
    dry_run_output = StringIO()

    status = await run_cli(
        ["repair", "state-events", "--confirmed", "true"],
        service=service,
        stdout=output,
    )
    rejected = await run_cli(
        ["repair", "state-events", "--confirmed", "false"],
        service=service,
        stderr=rejected_error,
    )
    dry_run = await run_cli(
        ["repair", "state-events", "--dry-run"],
        service=service,
        stdout=dry_run_output,
    )
    payload = read_json(output)
    dry_run_payload = read_json(dry_run_output)

    assert status == 0
    assert payload["status"] == "clean"
    assert payload["repaired_event_count"] == 0
    assert payload["skipped_item_count"] == 0
    assert dry_run == 0
    assert dry_run_payload["status"] == "clean"
    assert rejected == 2
    assert "confirmed=true" in read_json(rejected_error)["error"]["message"]


@pytest.mark.asyncio
async def test_cli_exposes_service_catalog_commands() -> None:
    service, _ = build_cli_service([])
    output = StringIO()

    status = await run_cli(["capabilities", "list"], service=service, stdout=output)
    payload = read_json(output)
    policies_output = StringIO()
    evaluators_output = StringIO()
    memory_output = StringIO()
    multi_agent_output = StringIO()
    policies_status = await run_cli(["policies", "list"], service=service, stdout=policies_output)
    evaluators_status = await run_cli(
        ["evaluators", "list"], service=service, stdout=evaluators_output
    )
    memory_status = await run_cli(["memory", "list"], service=service, stdout=memory_output)
    multi_agent_status = await run_cli(["multi-agent"], service=service, stdout=multi_agent_output)
    policies_payload = read_json(policies_output)
    evaluators_payload = read_json(evaluators_output)
    memory_payload = read_json(memory_output)
    multi_agent_payload = read_json(multi_agent_output)

    assert status == 0
    assert policies_status == 0
    assert evaluators_status == 0
    assert memory_status == 0
    assert multi_agent_status == 0
    capabilities = payload["capabilities"]
    assert isinstance(capabilities, list)
    scale = next(
        item for item in capabilities if isinstance(item, dict) and item["name"] == "scale_workload"
    )
    assert scale["required_arguments"] == ["name", "namespace", "replicas"]
    assert isinstance(scale["argument_schema"], dict)
    assert {item["name"] for item in capabilities if isinstance(item, dict)} >= {
        "inspect_workload",
        "scale_workload",
    }
    policies = policies_payload["policies"]
    evaluators = evaluators_payload["evaluators"]
    assert {item["name"] for item in policies if isinstance(item, dict)} >= {
        "kubernetes-read-only",
        "kubernetes-scale-safety",
    }
    assert {item["name"] for item in evaluators if isinstance(item, dict)} == {"workload-health"}
    memories = memory_payload["memories"]
    assert {item["subject"] for item in memories if isinstance(item, dict)} >= {
        "kubernetes readiness",
        "unhealthy workload triage",
    }
    assert multi_agent_payload["enabled"] is False
    assert multi_agent_payload["profile_count"] == 0


@pytest.mark.asyncio
async def test_cli_exposes_domain_package_catalog_commands() -> None:
    service, _ = build_cli_service([], domain_packages=package_registry())
    list_output = StringIO()
    filtered_output = StringIO()
    show_output = StringIO()
    verify_output = StringIO()
    local_path_verify_output = StringIO()
    missing_output = StringIO()
    missing_error = StringIO()

    list_status = await run_cli(
        ["domain-packages", "list"],
        service=service,
        stdout=list_output,
    )
    filtered_status = await run_cli(
        ["domain-packages", "list", "--tag", "ops"],
        service=service,
        stdout=filtered_output,
    )
    show_status = await run_cli(
        ["domain-packages", "show", "kubernetes", "0.2.0"],
        service=service,
        stdout=show_output,
    )
    verify_status = await run_cli(
        ["domain-packages", "verify"],
        service=service,
        stdout=verify_output,
    )
    local_path_verify_status = await run_cli(
        ["domain-packages", "verify", "--local-paths"],
        service=service,
        stdout=local_path_verify_output,
    )
    missing_status = await run_cli(
        ["domain-packages", "show", "database"],
        service=service,
        stdout=missing_output,
        stderr=missing_error,
    )

    listed = read_json(list_output)
    filtered = read_json(filtered_output)
    shown = read_json(show_output)
    verification = read_json(verify_output)
    local_path_verification = read_json(local_path_verify_output)
    packages = listed["domain_packages"]
    assert list_status == 0
    assert filtered_status == 0
    assert show_status == 0
    assert verify_status == 0
    assert local_path_verify_status == 0
    assert missing_status == 1
    assert missing_output.getvalue() == ""
    assert "domain package not registered: database" in missing_error.getvalue()
    assert isinstance(packages, list)
    assert len(packages) == 1
    package = packages[0]
    assert isinstance(package, dict)
    assert package["name"] == "kubernetes"
    assert package["version"] == "0.2.0"
    assert package["capability_names"] == ["inspect_workload", "scale_workload"]
    assert package["required_tools"] == ["kubernetes_api"]
    assert package["resource_names"] == ["resources/runbook.md"]
    assert package["security"] == {"side_effects": "reversible"}
    assert filtered == listed
    assert shown == package
    assert verification["passed"] is False
    assert verification["failed_check_count"] == 1
    assert verification["checks"][0]["name"] == "package_dependencies_registered"
    assert "observability@1.0.0" in verification["checks"][0]["message"]
    local_path_failed = {
        check["name"]: check["message"]
        for check in local_path_verification["checks"]
        if isinstance(check, dict) and check["passed"] is False
    }
    assert local_path_verification["failed_check_count"] == 5
    assert "package_root_exists:kubernetes@0.2.0" in local_path_failed
    assert "package_manifest_exists:kubernetes@0.2.0" in local_path_failed
    assert "package_manifest_matches_identity:kubernetes@0.2.0" in local_path_failed
    assert "package_resources_exist:kubernetes@0.2.0" in local_path_failed


@pytest.mark.asyncio
async def test_cli_loads_domain_package_runtime_entrypoint(tmp_path: Path) -> None:
    service, _ = build_cli_service([])
    package_root = tmp_path / "widget-domain"
    mismatch_root = tmp_path / "mismatch-domain"
    write_runtime_domain_package_file(
        package_root,
        module_name="widget_domain_cli_runtime",
    )
    write_runtime_domain_package_file(
        mismatch_root,
        module_name="widget_domain_cli_mismatch",
        capability_name="observe_widget",
    )
    output = StringIO()
    mismatch_output = StringIO()
    mismatch_error = StringIO()

    status = await run_cli(
        ["domain-packages", "load-runtime", str(package_root)],
        service=service,
        stdout=output,
    )
    mismatch_status = await run_cli(
        ["domain-packages", "load-runtime", str(mismatch_root)],
        service=service,
        stdout=mismatch_output,
        stderr=mismatch_error,
    )
    payload = read_json(output)

    assert status == 0
    assert payload["status"] == "loaded"
    assert payload["metadata_verified"] is True
    assert payload["package"] == {
        "name": "widget",
        "version": "1.0.0",
        "entrypoint": "widget_domain_cli_runtime:build_domain",
        "root_path": str(package_root),
        "manifest_path": str(package_root / "manifest.json"),
    }
    active_domain = payload["active_domain"]
    assert isinstance(active_domain, dict)
    assert active_domain["name"] == "widget"
    assert active_domain["version"] == "1.0.0"
    assert active_domain["capability_names"] == ["inspect_widget"]
    assert active_domain["tool_names"] == ["inspect_widget"]
    assert active_domain["evaluator_names"] == ["criteria"]
    assert mismatch_status == 2
    assert mismatch_output.getvalue() == ""
    assert "capabilities mismatch" in read_json(mismatch_error)["error"]["message"]


@pytest.mark.asyncio
async def test_cli_scaffolds_domain_package(tmp_path: Path) -> None:
    service, _ = build_cli_service([])
    package_root = tmp_path / "ai-ops-domain"
    output = StringIO()
    duplicate_output = StringIO()
    duplicate_error = StringIO()
    force_output = StringIO()

    status = await run_cli(
        [
            "domain-packages",
            "scaffold",
            "ai-ops",
            "--description",
            "AI operations domain package",
            "--output",
            str(package_root),
            "--version",
            "1.0.0",
            "--author",
            "Runtime Team",
            "--ontology",
            "Incident",
            "--capability",
            "inspect_incident",
            "--tool",
            "incident_api_get",
            "--policy",
            "incident_safety",
            "--procedure",
            "diagnose_incident",
            "--knowledge",
            "incident lifecycle",
            "--evaluator",
            "incident_status",
            "--context-provider",
            "incident_context",
            "--prompt",
            "incident_prompt",
            "--resource",
            "resources/runbook.md",
            "--resource",
            "schemas/incident.json",
            "--dependency",
            "observability@1.0.0",
            "--required-tool",
            "incident_api",
            "--runtime-api",
            ">=0.1,<1",
            "--domain-api",
            "agent.nantian.dev/v1alpha1",
            "--side-effects",
            "reversible",
            "--requires-confirmation",
            "--tag",
            "ops",
        ],
        service=service,
        stdout=output,
    )
    duplicate_status = await run_cli(
        [
            "domain-packages",
            "scaffold",
            "ai-ops",
            "--description",
            "AI operations domain package",
            "--output",
            str(package_root),
        ],
        service=service,
        stdout=duplicate_output,
        stderr=duplicate_error,
    )
    force_status = await run_cli(
        [
            "domain-packages",
            "scaffold",
            "ai-ops",
            "--description",
            "Updated AI operations domain package",
            "--output",
            str(package_root),
            "--version",
            "1.1.0",
            "--resource",
            "resources/runbook.md",
            "--resource",
            "schemas/incident.json",
            "--force",
        ],
        service=service,
        stdout=force_output,
    )

    payload = read_json(output)
    force_payload = read_json(force_output)
    manifest = json.loads((package_root / "manifest.json").read_text(encoding="utf-8"))
    assert status == 0
    assert duplicate_status == 2
    assert force_status == 0
    assert payload["status"] == "created"
    assert payload["name"] == "ai-ops"
    assert payload["version"] == "1.0.0"
    assert payload["manifest_path"] == str(package_root / "manifest.json")
    assert "already exists" in duplicate_error.getvalue()
    assert force_payload["status"] == "updated"
    assert force_payload["version"] == "1.1.0"
    assert manifest["metadata"]["version"] == "1.1.0"
    assert manifest["entrypoint"] == "ai_ops.domain:build_domain"
    assert manifest["resources"] == ["resources/runbook.md", "schemas/incident.json"]
    assert (package_root / "capabilities").is_dir()
    assert (package_root / "context_providers").is_dir()
    assert (package_root / "tests").is_dir()
    assert (package_root / "resources" / "runbook.md").is_file()
    assert (package_root / "schemas" / "incident.json").is_file()


@pytest.mark.asyncio
async def test_cli_scaffolds_loadable_domain_runtime_stub(tmp_path: Path) -> None:
    service, _ = build_cli_service([])
    package_root = tmp_path / "widget-domain"
    scaffold_output = StringIO()
    load_output = StringIO()

    scaffold_status = await run_cli(
        [
            "domain-packages",
            "scaffold",
            "widget",
            "--description",
            "Widget inspection domain package",
            "--output",
            str(package_root),
            "--version",
            "1.0.0",
            "--ontology",
            "Widget",
            "--capability",
            "inspect_widget",
            "--tool",
            "inspect_widget",
            "--evaluator",
            "criteria",
            "--runtime-stub",
        ],
        service=service,
        stdout=scaffold_output,
    )
    load_status = await run_cli(
        ["domain-packages", "load-runtime", str(package_root)],
        service=service,
        stdout=load_output,
    )

    scaffold_payload = read_json(scaffold_output)
    load_payload = read_json(load_output)
    assert scaffold_status == 0
    assert load_status == 0
    assert str(package_root / "widget" / "__init__.py") in scaffold_payload["written_paths"]
    assert str(package_root / "widget" / "domain.py") in scaffold_payload["written_paths"]
    assert scaffold_payload["runtime_stub_paths"] == [
        str(package_root / "widget" / "__init__.py"),
        str(package_root / "widget" / "domain.py"),
    ]
    assert load_payload["status"] == "loaded"
    assert load_payload["metadata_verified"] is True
    assert load_payload["package"]["entrypoint"] == "widget.domain:build_domain"
    assert load_payload["active_domain"]["name"] == "widget"
    assert load_payload["active_domain"]["version"] == "1.0.0"
    assert load_payload["active_domain"]["capability_names"] == ["inspect_widget"]
    assert load_payload["active_domain"]["tool_names"] == ["inspect_widget"]
    assert load_payload["active_domain"]["evaluator_names"] == ["criteria"]


@pytest.mark.asyncio
async def test_cli_exposes_distributed_snapshot_and_health_commands() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()
    scheduled = coordinator.scheduler.schedule_session(SessionId("session-1"), available_at=now)
    coordinator.workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=999_999_999,
        now=now,
    )
    coordinator.queue.enqueue(kind="completed-maintenance-test", priority=10, available_at=now)
    completed_lease = coordinator.queue.lease(worker_id=WorkerId("worker-a"), now=now)
    assert completed_lease.lease is not None
    completed = coordinator.queue.complete(
        completed_lease.lease.lease_id,
        worker_id=WorkerId("worker-a"),
        now=now,
    )
    service, _ = build_cli_service([], distributed_coordinator=coordinator)
    snapshot_output = StringIO()
    health_output = StringIO()
    missing_output = StringIO()
    missing_error = StringIO()

    snapshot_status = await run_cli(
        ["distributed", "snapshot"],
        service=service,
        stdout=snapshot_output,
    )
    health_status = await run_cli(
        ["distributed", "health"],
        service=service,
        stdout=health_output,
    )
    default_output = StringIO()
    default_status = await run_cli(["distributed", "health"], stdout=default_output)
    expire_output = StringIO()
    expire_status = await run_cli(["distributed", "expire"], stdout=expire_output)
    prune_output = StringIO()
    prune_status = await run_cli(
        ["distributed", "prune-terminal", "--before", now.isoformat()],
        service=service,
        stdout=prune_output,
    )
    cancel_output = StringIO()
    cancel_status = await run_cli(
        [
            "distributed",
            "cancel",
            str(scheduled.work_item_id),
            "--reason",
            "operator cancelled distributed work",
        ],
        service=service,
        stdout=cancel_output,
    )
    missing_status = await run_cli(
        ["distributed", "health"],
        service=build_cli_service([])[0],
        stdout=missing_output,
        stderr=missing_error,
    )

    snapshot = read_json(snapshot_output)
    health = read_json(health_output)
    default_health = read_json(default_output)
    expire = read_json(expire_output)
    prune = read_json(prune_output)
    cancel = read_json(cancel_output)

    assert snapshot_status == 0
    assert snapshot["work_queue"]["queued_count"] == 1
    assert snapshot["workers"]["online_count"] == 1
    assert health_status == 0
    assert health["status"] == "ok"
    assert default_status == 0
    assert default_health["status"] == "ok"
    assert expire_status == 0
    assert expire["expired_work_items"] == []
    assert prune_status == 0
    assert prune["before"] == now.isoformat()
    assert prune["pruned_count"] == 1
    assert prune["pruned_work_items"][0]["work_item_id"] == str(completed.work_item_id)
    assert prune["snapshot"]["work_queue"]["total_count"] == 1
    assert cancel_status == 0
    assert cancel["cancelled_work_item"]["work_item_id"] == str(scheduled.work_item_id)
    assert cancel["cancelled_work_item"]["status"] == "cancelled"
    assert cancel["snapshot"]["work_queue"]["cancelled_count"] == 1
    assert missing_status == 2
    assert missing_output.getvalue() == ""
    assert "distributed runtime coordinator is not configured" in missing_error.getvalue()


@pytest.mark.asyncio
async def test_cli_distributed_schedule_session_command() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()
    coordinator.workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=999_999_999,
        now=now,
    )
    service, _ = build_cli_service([], distributed_coordinator=coordinator)
    output = StringIO()

    status = await run_cli(
        [
            "distributed",
            "schedule-session",
            "session-1",
            "--priority",
            "7",
            "--max-attempts",
            "2",
        ],
        service=service,
        stdout=output,
    )
    payload = read_json(output)

    assert status == 0
    assert payload["scheduled_work_item"]["kind"] == "agent_session"
    assert payload["scheduled_work_item"]["status"] == "queued"
    assert payload["snapshot"]["work_queue"]["queued_count"] == 1
    assert payload["health"]["status"] == "ok"


@pytest.mark.asyncio
async def test_cli_distributed_worker_run_once_resumes_scheduled_session() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, _ = build_cli_service(
        [wait(), inspect_workload(), finish()],
        distributed_coordinator=coordinator,
    )
    waiting = await service.run_goal(*goal_task())
    service.distributed_schedule_session(waiting.result.session_id)
    output = StringIO()

    status = await run_cli(
        [
            "distributed",
            "worker-run-once",
            "worker-a",
            "--lease-ttl-seconds",
            "30",
            "--worker-ttl-seconds",
            "30",
        ],
        service=service,
        stdout=output,
    )
    payload = read_json(output)
    completed = await service.get_session(waiting.result.session_id)

    assert status == 0
    assert payload["status"] == "completed"
    assert payload["worker_id"] == "worker-a"
    assert payload["work_item"]["status"] == "completed"
    assert completed.goal_status.value == "completed"


@pytest.mark.asyncio
async def test_cli_distributed_worker_run_until_idle_resumes_backlog() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, _ = build_cli_service(
        [
            wait(),
            wait(),
            inspect_workload(),
            finish(),
            inspect_workload(),
            finish(),
        ],
        distributed_coordinator=coordinator,
    )
    first = await service.run_goal(*goal_task())
    second = await service.run_goal(*goal_task())
    service.distributed_schedule_session(first.result.session_id)
    service.distributed_schedule_session(second.result.session_id)
    output = StringIO()

    status = await run_cli(
        [
            "distributed",
            "worker-run",
            "worker-a",
            "--max-items",
            "5",
            "--lease-ttl-seconds",
            "30",
            "--worker-ttl-seconds",
            "30",
        ],
        service=service,
        stdout=output,
    )
    payload = read_json(output)

    assert status == 0
    assert [item["status"] for item in payload["results"]] == [
        "completed",
        "completed",
        "no_work",
    ]
    assert payload["processed_count"] == 2
    assert payload["terminal_status"] == "no_work"
    assert (await service.get_session(first.result.session_id)).goal_status.value == "completed"
    assert (await service.get_session(second.result.session_id)).goal_status.value == "completed"


@pytest.mark.asyncio
async def test_cli_distributed_schedule_task_command_runs_from_worker() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, _ = build_cli_service(
        [wait(), inspect_workload(), finish()],
        distributed_coordinator=coordinator,
    )
    waiting = await service.run_goal(*goal_task())
    schedule_output = StringIO()
    worker_output = StringIO()

    schedule_status = await run_cli(
        [
            "distributed",
            "schedule-task",
            str(waiting.result.session_id),
            str(waiting.session.current_task_id),
            "--priority",
            "4",
        ],
        service=service,
        stdout=schedule_output,
    )
    worker_status = await run_cli(
        ["distributed", "worker-run-once", "worker-a"],
        service=service,
        stdout=worker_output,
    )
    scheduled = read_json(schedule_output)
    worker = read_json(worker_output)
    completed = await service.get_session(waiting.result.session_id)

    assert schedule_status == 0
    assert scheduled["scheduled_work_item"]["kind"] == "task"
    assert scheduled["scheduled_work_item"]["status"] == "queued"
    assert worker_status == 0
    assert worker["status"] == "completed"
    assert worker["work_item"]["status"] == "completed"
    assert completed.goal_status.value == "completed"


@pytest.mark.asyncio
async def test_cli_distributed_schedule_action_command_confirms_pending_action() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, backend = build_cli_service(
        [scale_workload(), inspect_workload(), finish()],
        distributed_coordinator=coordinator,
        environment="production",
    )
    waiting = await service.run_goal(*goal_task())
    assert waiting.session.pending_action is not None
    schedule_output = StringIO()
    worker_output = StringIO()

    schedule_status = await run_cli(
        [
            "distributed",
            "schedule-action",
            str(waiting.result.session_id),
            str(waiting.session.current_task_id),
            str(waiting.session.pending_action.action_id),
            "--confirmed",
            "true",
            "--priority",
            "4",
        ],
        service=service,
        stdout=schedule_output,
    )
    worker_status = await run_cli(
        ["distributed", "worker-run-once", "worker-a"],
        service=service,
        stdout=worker_output,
    )
    scheduled = read_json(schedule_output)
    worker = read_json(worker_output)
    completed = await service.get_session(waiting.result.session_id)

    assert schedule_status == 0
    assert scheduled["scheduled_work_item"]["kind"] == "tool_action"
    assert scheduled["scheduled_work_item"]["status"] == "queued"
    assert scheduled["scheduled_work_item"]["action_id"] == str(
        waiting.session.pending_action.action_id
    )
    assert worker_status == 0
    assert worker["status"] == "completed"
    assert worker["work_item"]["status"] == "completed"
    assert completed.goal_status.value == "completed"
    assert completed.pending_action is None
    assert backend.mutation_calls == 1


@pytest.mark.asyncio
async def test_cli_distributed_schedule_pending_actions_command_confirms_pending_actions() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, backend = build_cli_service(
        [scale_workload(), inspect_workload(), finish()],
        distributed_coordinator=coordinator,
        environment="production",
    )
    waiting = await service.run_goal(*goal_task())
    assert waiting.session.pending_action is not None
    pending = waiting.session.pending_action
    schedule_output = StringIO()
    worker_output = StringIO()

    schedule_status = await run_cli(
        [
            "distributed",
            "schedule-pending-actions",
            "--confirmed",
            "true",
            "--priority",
            "4",
        ],
        service=service,
        stdout=schedule_output,
    )
    worker_status = await run_cli(
        ["distributed", "worker-run-once", "worker-a"],
        service=service,
        stdout=worker_output,
    )
    scheduled = read_json(schedule_output)
    worker = read_json(worker_output)
    completed = await service.get_session(waiting.result.session_id)

    assert schedule_status == 0
    assert scheduled["scheduled_count"] == 1
    assert scheduled["scheduled_work_items"][0]["kind"] == "tool_action"
    assert scheduled["scheduled_work_items"][0]["status"] == "queued"
    assert scheduled["scheduled_work_items"][0]["action_id"] == str(pending.action_id)
    assert worker_status == 0
    assert worker["status"] == "completed"
    assert worker["work_item"]["status"] == "completed"
    assert completed.goal_status.value == "completed"
    assert completed.pending_action is None
    assert backend.mutation_calls == 1


@pytest.mark.asyncio
async def test_cli_distributed_schedule_goal_command_runs_from_worker() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, _ = build_cli_service(
        [inspect_workload(), finish()],
        distributed_coordinator=coordinator,
    )
    schedule_output = StringIO()
    worker_output = StringIO()

    schedule_status = await run_cli(
        [
            "distributed",
            "schedule-goal",
            "production-operator",
            "Verify workload from scheduled goal",
            "--task",
            "Inspect workload",
            "--success",
            'resource="deployment/example"',
            "--priority",
            "4",
        ],
        service=service,
        stdout=schedule_output,
    )
    worker_status = await run_cli(
        ["distributed", "worker-run-once", "worker-a"],
        service=service,
        stdout=worker_output,
    )
    scheduled = read_json(schedule_output)
    worker = read_json(worker_output)
    sessions = await service.list_sessions()

    assert schedule_status == 0
    assert scheduled["scheduled_work_item"]["kind"] == "agent_goal"
    assert scheduled["scheduled_work_item"]["status"] == "queued"
    assert worker_status == 0
    assert worker["status"] == "completed"
    assert worker["work_item"]["status"] == "completed"
    assert len(sessions) == 1
    assert sessions[0].goal_description == "Verify workload from scheduled goal"
    assert sessions[0].goal_status.value == "completed"
    completed = await service.get_session(sessions[0].session_id)
    assert completed.satisfied_criteria["resource"] == "deployment/example"
    assert completed.tasks[0].required_criteria == ("resource",)


@pytest.mark.asyncio
async def test_cli_distributed_lock_lifecycle_commands() -> None:
    service, _ = build_cli_service([], distributed_coordinator=DistributedRuntimeCoordinator())
    acquire_output = StringIO()
    heartbeat_output = StringIO()
    release_output = StringIO()

    acquire_status = await run_cli(
        [
            "distributed",
            "lock-acquire",
            "session/session-1",
            "--owner-id",
            "worker-a",
            "--ttl-seconds",
            "30",
        ],
        service=service,
        stdout=acquire_output,
    )
    acquired = read_json(acquire_output)
    lease_id = acquired["lock"]["lease_id"]
    heartbeat_status = await run_cli(
        [
            "distributed",
            "lock-heartbeat",
            lease_id,
            "--owner-id",
            "worker-a",
            "--ttl-seconds",
            "60",
        ],
        service=service,
        stdout=heartbeat_output,
    )
    release_status = await run_cli(
        [
            "distributed",
            "lock-release",
            lease_id,
            "--owner-id",
            "worker-a",
        ],
        service=service,
        stdout=release_output,
    )

    heartbeat = read_json(heartbeat_output)
    released = read_json(release_output)

    assert acquire_status == 0
    assert heartbeat_status == 0
    assert release_status == 0
    assert acquired["lock"]["lock_key"] == "session/session-1"
    assert heartbeat["lock"]["lease_id"] == lease_id
    assert released["snapshot"]["locks"] == []


@pytest.mark.asyncio
async def test_cli_distributed_worker_lifecycle_commands() -> None:
    service, _ = build_cli_service([], distributed_coordinator=DistributedRuntimeCoordinator())
    register_output = StringIO()
    heartbeat_output = StringIO()
    drain_output = StringIO()
    offline_output = StringIO()

    register_status = await run_cli(
        [
            "distributed",
            "worker-register",
            "worker-a",
            "--capability",
            "agent_session",
            "--ttl-seconds",
            "30",
        ],
        service=service,
        stdout=register_output,
    )
    heartbeat_status = await run_cli(
        [
            "distributed",
            "worker-heartbeat",
            "worker-a",
            "--ttl-seconds",
            "60",
        ],
        service=service,
        stdout=heartbeat_output,
    )
    drain_status = await run_cli(
        [
            "distributed",
            "worker-drain",
            "worker-a",
            "--reason",
            "finish current lease",
        ],
        service=service,
        stdout=drain_output,
    )
    offline_status = await run_cli(
        [
            "distributed",
            "worker-offline",
            "worker-a",
            "--reason",
            "shutdown complete",
        ],
        service=service,
        stdout=offline_output,
    )

    registered = read_json(register_output)
    heartbeat = read_json(heartbeat_output)
    draining = read_json(drain_output)
    offline = read_json(offline_output)

    assert register_status == 0
    assert heartbeat_status == 0
    assert drain_status == 0
    assert offline_status == 0
    assert registered["worker"]["worker_id"] == "worker-a"
    assert registered["worker"]["capabilities"] == ["agent_session"]
    assert heartbeat["worker"]["status"] == "online"
    assert draining["worker"]["status"] == "draining"
    assert offline["worker"]["status"] == "offline"
    assert offline["snapshot"]["workers"]["offline_count"] == 1


@pytest.mark.asyncio
async def test_cli_config_show_exposes_runtime_configuration() -> None:
    service, _ = build_cli_service([])
    output = StringIO()

    status = await run_cli(["config", "show"], service=service, stdout=output)
    payload = read_json(output)

    assert status == 0
    assert payload == {
        "environment": {"environment": "staging"},
        "model": {"provider": "scripted", "name": "scripted", "timeout_seconds": 30.0},
        "secrets": [],
        "store": {"backend": "memory", "path": None},
        "state_event_commit": {
            "supported": False,
            "strategy": "split_store",
            "shared_store": False,
        },
        "distributed_queue": {"backend": "memory", "path": None},
        "distributed_locks": {"backend": "memory", "path": None},
        "distributed_workers": {"backend": "memory", "path": None},
        "distributed_terminal_retention_seconds": None,
        "limits": {"max_iterations": 12, "max_recovery_steps": 4},
        "domains": [{"name": "kubernetes", "version": "0.2.0", "primary": True}],
    }


@pytest.mark.asyncio
async def test_cli_controls_waiting_session_lifecycle_through_service() -> None:
    service, backend = build_cli_service([wait(), inspect_workload(), finish()])
    waiting = await service.run_goal(*goal_task())
    session_id = str(waiting.result.session_id)
    list_output = StringIO()
    pause_output = StringIO()
    events_output = StringIO()
    evidence_output = StringIO()
    world_output = StringIO()
    sse_events_output = StringIO()
    resume_output = StringIO()

    list_status = await run_cli(
        ["session", "list"],
        service=service,
        stdout=list_output,
    )
    pause_status = await run_cli(
        ["session", "pause", session_id, "--reason", "operator paused from test"],
        service=service,
        stdout=pause_output,
    )
    events_status = await run_cli(
        ["session", "events", session_id, "--limit", "2"],
        service=service,
        stdout=events_output,
    )
    evidence_status = await run_cli(
        ["session", "evidence", session_id],
        service=service,
        stdout=evidence_output,
    )
    world_status = await run_cli(
        ["session", "world", session_id],
        service=service,
        stdout=world_output,
    )
    sse_events_status = await run_cli(
        ["session", "events", session_id, "--limit", "2", "--format", "sse"],
        service=service,
        stdout=sse_events_output,
    )
    resume_status = await run_cli(
        ["session", "resume", session_id], service=service, stdout=resume_output
    )

    list_payload = read_json(list_output)
    pause_payload = read_json(pause_output)
    events_payload = read_json(events_output)
    evidence_payload = read_json(evidence_output)
    world_payload = read_json(world_output)
    sse_events = sse_events_output.getvalue()
    resume_payload = read_json(resume_output)
    assert list_status == 0
    assert pause_status == 0
    assert events_status == 0
    assert evidence_status == 0
    assert world_status == 0
    assert sse_events_status == 0
    assert resume_status == 0
    session_items = list_payload["sessions"]
    assert isinstance(session_items, list)
    assert len(session_items) == 1
    listed_session = session_items[0]
    assert isinstance(listed_session, dict)
    assert listed_session["session_id"] == session_id
    assert listed_session["goal_status"] == "waiting"
    assert listed_session["pending_action"] is False
    assert pause_payload["result"]["status"] == "waiting"
    assert len(events_payload["events"]) == 2
    assert events_payload["next_cursor"] == events_payload["events"][-1]["event_id"]
    assert evidence_payload["session_id"] == session_id
    assert world_payload["session_id"] == session_id
    evidence_items = evidence_payload["evidence"]
    world_items = world_payload["world_facts"]
    assert isinstance(evidence_items, list)
    assert isinstance(world_items, list)
    assert evidence_items == []
    assert world_items == []
    assert world_payload["world_entities"] == []
    assert world_payload["world_relations"] == []
    assert "event: GoalCreated\n" in sse_events
    assert "data: " in sse_events
    assert ": next_cursor=" in sse_events
    assert resume_payload["result"]["status"] == "completed"
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_session_events_can_wait_for_new_events() -> None:
    service, _ = build_cli_service([wait(), inspect_workload(), finish()])
    waiting = await service.run_goal(*goal_task())
    session_id = str(waiting.result.session_id)
    existing = await service.stream_events(SessionId(session_id))
    last_cursor = existing.events[-1].event_id
    wait_output = StringIO()
    resume_output = StringIO()

    wait_task = asyncio.create_task(
        run_cli(
            [
                "session",
                "events",
                session_id,
                "--after",
                last_cursor,
                "--wait",
                "--timeout-seconds",
                "1",
                "--poll-interval-seconds",
                "0.001",
            ],
            service=service,
            stdout=wait_output,
        )
    )
    await asyncio.sleep(0.01)
    resume_status = await run_cli(
        ["session", "resume", session_id],
        service=service,
        stdout=resume_output,
    )
    wait_status = await wait_task
    wait_payload = read_json(wait_output)

    assert resume_status == 0
    assert wait_status == 0
    events = wait_payload["events"]
    assert isinstance(events, list)
    assert events
    assert wait_payload["next_cursor"] == events[-1]["event_id"]
    assert any(item["type"] in {"StateUpdated", "GoalCompleted"} for item in events)


@pytest.mark.asyncio
async def test_cli_session_events_rejects_invalid_wait_timeout() -> None:
    service, _ = build_cli_service([wait()])
    waiting = await service.run_goal(*goal_task())
    output = StringIO()
    error = StringIO()

    status = await run_cli(
        [
            "session",
            "events",
            str(waiting.result.session_id),
            "--wait",
            "--timeout-seconds",
            "31",
        ],
        service=service,
        stdout=output,
        stderr=error,
    )
    payload = read_json(error)

    assert status == 2
    assert payload == {
        "error": {
            "code": "bad_request",
            "message": "timeout_seconds must be between 0 and 30",
        }
    }
    assert output.getvalue() == ""


@pytest.mark.asyncio
async def test_cli_session_list_supports_cursor_and_limit() -> None:
    service, _ = build_cli_service(
        [
            inspect_workload(),
            finish(),
            inspect_workload(),
            finish(),
            inspect_workload(),
            finish(),
        ]
    )
    for index in range(3):
        await service.run_goal(
            Goal(f"Verify workload {index}", (SuccessCriterion("healthy", True),)),
            Task(f"Inspect workload {index}", ("healthy",)),
        )
    first_output = StringIO()
    second_output = StringIO()

    first_status = await run_cli(
        ["session", "list", "--limit", "2"],
        service=service,
        stdout=first_output,
    )
    first_payload = read_json(first_output)
    first_sessions = first_payload["sessions"]
    assert isinstance(first_sessions, list)
    cursor = first_payload["next_cursor"]
    assert isinstance(cursor, str)
    second_status = await run_cli(
        ["session", "list", "--after", cursor, "--limit", "2"],
        service=service,
        stdout=second_output,
    )
    second_payload = read_json(second_output)
    second_sessions = second_payload["sessions"]

    assert first_status == 0
    assert second_status == 0
    assert len(first_sessions) == 2
    assert cursor == first_sessions[-1]["session_id"]
    assert isinstance(second_sessions, list)
    assert len(second_sessions) == 1
    assert second_payload["next_cursor"] == second_sessions[-1]["session_id"]


@pytest.mark.asyncio
async def test_cli_exposes_operations_commands_through_service() -> None:
    service, backend = build_cli_service(
        [scale_workload(), inspect_workload(), finish()],
        usage=[
            ModelUsage(
                "scripted",
                "cli-test",
                input_tokens=80,
                output_tokens=20,
                estimated_cost_micros=18,
            ),
            ModelUsage(
                "scripted",
                "cli-test",
                input_tokens=40,
                output_tokens=10,
                estimated_cost_micros=9,
            ),
            ModelUsage("scripted", "cli-test", input_tokens=10, output_tokens=5),
        ],
    )
    run = await service.run_goal(*goal_task())
    session_id = str(run.result.session_id)
    metrics_output = StringIO()
    prometheus_metrics_output = StringIO()
    cost_output = StringIO()
    doctor_output = StringIO()
    audit_output = StringIO()
    session_audit_output = StringIO()
    session_cost_output = StringIO()
    logs_output = StringIO()
    session_logs_output = StringIO()
    traces_output = StringIO()
    session_traces_output = StringIO()
    otlp_traces_output = StringIO()
    session_otlp_traces_output = StringIO()

    metrics_status = await run_cli(["metrics"], service=service, stdout=metrics_output)
    prometheus_metrics_status = await run_cli(
        ["metrics", "--format", "prometheus"],
        service=service,
        stdout=prometheus_metrics_output,
    )
    cost_status = await run_cli(["cost"], service=service, stdout=cost_output)
    logs_status = await run_cli(["logs"], service=service, stdout=logs_output)
    traces_status = await run_cli(["traces"], service=service, stdout=traces_output)
    otlp_traces_status = await run_cli(
        ["traces", "--format", "otlp"],
        service=service,
        stdout=otlp_traces_output,
    )
    doctor_status = await run_cli(["doctor"], service=service, stdout=doctor_output)
    audit_status = await run_cli(["audit"], service=service, stdout=audit_output)
    session_audit_status = await run_cli(
        ["session", "audit", session_id],
        service=service,
        stdout=session_audit_output,
    )
    session_cost_status = await run_cli(
        ["session", "cost", session_id],
        service=service,
        stdout=session_cost_output,
    )
    session_logs_status = await run_cli(
        ["session", "logs", session_id],
        service=service,
        stdout=session_logs_output,
    )
    session_traces_status = await run_cli(
        ["session", "traces", session_id],
        service=service,
        stdout=session_traces_output,
    )
    session_otlp_traces_status = await run_cli(
        ["session", "traces", session_id, "--format", "otlp"],
        service=service,
        stdout=session_otlp_traces_output,
    )

    metrics = read_json(metrics_output)
    cost = read_json(cost_output)
    logs = read_json(logs_output)
    traces = read_json(traces_output)
    otlp_traces = read_json(otlp_traces_output)
    doctor = read_json(doctor_output)
    audit = read_json(audit_output)
    session_audit = read_json(session_audit_output)
    session_cost = read_json(session_cost_output)
    session_logs = read_json(session_logs_output)
    session_traces = read_json(session_traces_output)
    session_otlp_traces = read_json(session_otlp_traces_output)
    assert metrics_status == 0
    assert prometheus_metrics_status == 0
    assert cost_status == 0
    assert logs_status == 0
    assert traces_status == 0
    assert otlp_traces_status == 0
    assert doctor_status == 0
    assert audit_status == 0
    assert session_audit_status == 0
    assert session_cost_status == 0
    assert session_logs_status == 0
    assert session_traces_status == 0
    assert session_otlp_traces_status == 0
    assert metrics["completed_goal_count"] == 1
    assert metrics["action_started_count"] == 2
    assert metrics["model_call_count"] == 3
    assert metrics["model_total_token_count"] == 165
    prometheus_metrics = prometheus_metrics_output.getvalue()
    assert "universal_agent_runtime_completed_goals 1\n" in prometheus_metrics
    assert "universal_agent_runtime_action_started_count" not in prometheus_metrics
    assert "universal_agent_runtime_actions_started 2\n" in prometheus_metrics
    assert "universal_agent_runtime_model_total_tokens 165\n" in prometheus_metrics
    assert cost == session_cost
    assert cost["model_call_count"] == 3
    assert cost["total_tokens"] == 165
    assert cost["estimated_cost_micros"] == 27
    assert logs == session_logs
    log_items = logs["logs"]
    assert isinstance(log_items, list)
    assert log_items[-1]["event_type"] == "GoalCompleted"
    assert traces == session_traces
    span_items = traces["spans"]
    assert isinstance(span_items, list)
    assert [
        item["name"]
        for item in span_items
        if isinstance(item, dict) and str(item["name"]).startswith("runtime.action.")
    ] == [
        "runtime.action.scale_workload",
        "runtime.action.inspect_workload",
    ]
    phase_span_names = {
        item["name"]
        for item in span_items
        if isinstance(item, dict) and not str(item["name"]).startswith("runtime.action.")
    }
    assert phase_span_names >= {
        "runtime.session",
        "runtime.decision",
        "runtime.model_usage",
        "runtime.policy",
        "runtime.observation",
        "runtime.evaluation",
    }
    assert otlp_traces == session_otlp_traces
    resource_spans = otlp_traces["resourceSpans"]
    assert isinstance(resource_spans, list)
    resource_span = resource_spans[0]
    assert isinstance(resource_span, dict)
    scope_spans = resource_span["scopeSpans"]
    assert isinstance(scope_spans, list)
    scope_span = scope_spans[0]
    assert isinstance(scope_span, dict)
    exported_spans = scope_span["spans"]
    assert isinstance(exported_spans, list)
    assert len(exported_spans) > 3
    assert doctor["status"] == "ok"
    assert audit == session_audit
    audit_items = audit["audit_records"]
    assert isinstance(audit_items, list)
    assert len(audit_items) == 1
    record = audit_items[0]
    assert isinstance(record, dict)
    assert record["session_id"] == session_id
    assert record["capability"] == "scale_workload"
    assert record["status"] == "succeeded"
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_serve_starts_agentd_http_server_with_injected_runner() -> None:
    service, _ = build_cli_service([])
    output = StringIO()
    observed_urls: list[str] = []

    def runner(server: AgentdHttpServer) -> None:
        observed_urls.append(server.base_url)

    try:
        status = await run_cli(
            ["serve", "--port", "0"],
            service=service,
            server_runner=runner,
            stdout=output,
        )
    except PermissionError as exc:
        pytest.skip(f"local socket bind unavailable: {exc}")
    payload = read_json(output)

    assert status == 0
    assert payload["status"] == "serving"
    assert payload["base_url"] == observed_urls[0]
    assert payload["base_url"].startswith("http://127.0.0.1:")
    assert payload["auth_required"] is False
    assert payload["read_only_auth_enabled"] is False
    assert payload["evaluation_report_dir"] is None


@pytest.mark.asyncio
async def test_cli_serve_exposes_evaluation_report_dir(tmp_path: Path) -> None:
    service, _ = build_cli_service([])
    output = StringIO()
    observed_urls: list[str] = []
    report_dir = tmp_path / "reports"

    def runner(server: AgentdHttpServer) -> None:
        observed_urls.append(server.base_url)

    try:
        status = await run_cli(
            ["serve", "--port", "0", "--evaluation-report-dir", str(report_dir)],
            service=service,
            server_runner=runner,
            stdout=output,
        )
    except PermissionError as exc:
        pytest.skip(f"local socket bind unavailable: {exc}")
    payload = read_json(output)

    assert status == 0
    assert payload["status"] == "serving"
    assert payload["base_url"] == observed_urls[0]
    assert payload["evaluation_report_dir"] == str(report_dir)


@pytest.mark.asyncio
async def test_cli_serve_can_enable_agentd_bearer_auth() -> None:
    service, _ = build_cli_service([])
    output = StringIO()
    observed_urls: list[str] = []

    def runner(server: AgentdHttpServer) -> None:
        observed_urls.append(server.base_url)

    try:
        status = await run_cli(
            ["serve", "--port", "0", "--auth-token", "cli-token"],
            service=service,
            server_runner=runner,
            stdout=output,
        )
    except PermissionError as exc:
        pytest.skip(f"local socket bind unavailable: {exc}")
    payload = read_json(output)

    assert status == 0
    assert payload["status"] == "serving"
    assert payload["base_url"] == observed_urls[0]
    assert payload["auth_required"] is True
    assert payload["read_only_auth_enabled"] is False


@pytest.mark.asyncio
async def test_cli_serve_can_enable_read_only_agentd_bearer_auth() -> None:
    service, _ = build_cli_service([])
    output = StringIO()
    observed_urls: list[str] = []

    def runner(server: AgentdHttpServer) -> None:
        observed_urls.append(server.base_url)

    try:
        status = await run_cli(
            ["serve", "--port", "0", "--read-only-auth-token", "reader-token"],
            service=service,
            server_runner=runner,
            stdout=output,
        )
    except PermissionError as exc:
        pytest.skip(f"local socket bind unavailable: {exc}")
    payload = read_json(output)

    assert status == 0
    assert payload["status"] == "serving"
    assert payload["base_url"] == observed_urls[0]
    assert payload["auth_required"] is True
    assert payload["read_only_auth_enabled"] is True


@pytest.mark.asyncio
async def test_cli_serve_can_enable_agentd_bearer_auth_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_cli_service([])
    output = StringIO()
    observed_urls: list[str] = []
    monkeypatch.setenv("AGENTD_AUTH_TOKEN", "env-token")

    def runner(server: AgentdHttpServer) -> None:
        observed_urls.append(server.base_url)

    try:
        status = await run_cli(
            ["serve", "--port", "0", "--auth-token-env", "AGENTD_AUTH_TOKEN"],
            service=service,
            server_runner=runner,
            stdout=output,
        )
    except PermissionError as exc:
        pytest.skip(f"local socket bind unavailable: {exc}")
    payload = read_json(output)

    assert status == 0
    assert payload["status"] == "serving"
    assert payload["base_url"] == observed_urls[0]
    assert payload["auth_required"] is True
    assert payload["read_only_auth_enabled"] is False
    assert "env-token" not in output.getvalue()


@pytest.mark.asyncio
async def test_cli_serve_rejects_missing_agentd_bearer_auth_env() -> None:
    service, _ = build_cli_service([])
    output = StringIO()
    error = StringIO()
    observed_urls: list[str] = []

    def runner(server: AgentdHttpServer) -> None:
        observed_urls.append(server.base_url)

    status = await run_cli(
        ["serve", "--port", "0", "--auth-token-env", "MISSING_AGENTD_AUTH_TOKEN"],
        service=service,
        server_runner=runner,
        stdout=output,
        stderr=error,
    )

    assert status == 2
    assert observed_urls == []
    assert output.getvalue() == ""
    assert "agentd auth token env key is missing or empty" in error.getvalue()


@pytest.mark.asyncio
async def test_cli_serve_rejects_ambiguous_agentd_bearer_auth_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = build_cli_service([])
    error = StringIO()
    observed_urls: list[str] = []
    monkeypatch.setenv("AGENTD_AUTH_TOKEN", "env-token")

    def runner(server: AgentdHttpServer) -> None:
        observed_urls.append(server.base_url)

    status = await run_cli(
        [
            "serve",
            "--port",
            "0",
            "--auth-token",
            "literal-token",
            "--auth-token-env",
            "AGENTD_AUTH_TOKEN",
        ],
        service=service,
        server_runner=runner,
        stderr=error,
    )

    assert status == 2
    assert observed_urls == []
    assert "accepts either a literal value or env key" in error.getvalue()


@pytest.mark.asyncio
async def test_cli_eval_list_applies_kind_and_tag_filters() -> None:
    service, _ = build_cli_service([])
    output = StringIO()

    status = await run_cli(
        [
            "eval",
            "list",
            "production-operator",
            "--kind",
            "policy",
            "--tag",
            "kubernetes",
        ],
        service=service,
        stdout=output,
    )
    payload = read_json(output)

    assert status == 0
    assert payload["suite_name"] == "local evaluation suite"
    assert payload["suite_tags"] == ["local", "kubernetes"]
    assert payload["scenario_count"] == 1
    assert payload["scenarios"] == [
        {
            "scenario_name": "invalid scale policy",
            "kind": "policy",
            "tags": ["policy", "kubernetes"],
            "goal": {
                "description": "Evaluate workload health",
                "success_criteria": ["healthy"],
            },
            "task": {
                "description": "Inspect workload",
                "required_criteria": ["healthy"],
            },
        }
    ]


@pytest.mark.asyncio
async def test_cli_eval_list_loads_suite_file(tmp_path: Path) -> None:
    service, _ = build_cli_service([])
    suite_path = tmp_path / "suite.json"
    write_evaluation_suite_file(suite_path)
    output = StringIO()

    status = await run_cli(
        [
            "eval",
            "list",
            "production-operator",
            "--suite-file",
            str(suite_path),
            "--kind",
            "regression",
            "--tag",
            "file",
        ],
        service=service,
        stdout=output,
    )
    payload = read_json(output)

    assert status == 0
    assert payload["suite_name"] == "file evaluation suite"
    assert payload["suite_tags"] == ["file", "kubernetes"]
    assert payload["scenario_count"] == 1
    assert payload["scenarios"][0]["scenario_name"] == "file healthy workload"
    assert payload["scenarios"][0]["task"]["description"] == "Inspect workload from file"


@pytest.mark.asyncio
async def test_cli_eval_exposes_dataset_catalog(tmp_path: Path) -> None:
    service, _ = build_cli_service([])
    dataset_root = tmp_path / "datasets" / "kubernetes"
    write_evaluation_dataset_file(dataset_root)
    list_output = StringIO()
    filtered_output = StringIO()
    show_output = StringIO()
    verify_output = StringIO()
    missing_output = StringIO()
    missing_error = StringIO()

    list_status = await run_cli(
        ["eval", "datasets", "--dataset-dir", str(tmp_path / "datasets")],
        service=service,
        stdout=list_output,
    )
    filtered_status = await run_cli(
        [
            "eval",
            "datasets",
            "--dataset-dir",
            str(tmp_path / "datasets"),
            "--tag",
            "kubernetes",
            "--domain",
            "kubernetes@0.2.0",
        ],
        service=service,
        stdout=filtered_output,
    )
    show_status = await run_cli(
        [
            "eval",
            "dataset",
            "kubernetes-remediation",
            "1.0.0",
            "--dataset-dir",
            str(tmp_path / "datasets"),
        ],
        service=service,
        stdout=show_output,
    )
    verify_status = await run_cli(
        ["eval", "datasets", "--dataset-dir", str(tmp_path / "datasets"), "--verify"],
        service=service,
        stdout=verify_output,
    )
    missing_status = await run_cli(
        [
            "eval",
            "dataset",
            "database",
            "--dataset-dir",
            str(tmp_path / "datasets"),
        ],
        service=service,
        stdout=missing_output,
        stderr=missing_error,
    )

    listed = read_json(list_output)
    filtered = read_json(filtered_output)
    shown = read_json(show_output)
    verification = read_json(verify_output)
    datasets = listed["datasets"]
    assert list_status == 0
    assert filtered_status == 0
    assert show_status == 0
    assert verify_status == 0
    assert missing_status == 1
    assert missing_output.getvalue() == ""
    assert "evaluation dataset not registered: database" in missing_error.getvalue()
    assert isinstance(datasets, list)
    assert len(datasets) == 1
    dataset = datasets[0]
    assert isinstance(dataset, dict)
    assert dataset["name"] == "kubernetes-remediation"
    assert dataset["version"] == "1.0.0"
    assert dataset["suite_count"] == 1
    assert dataset["domains"] == [{"name": "kubernetes", "version": "0.2.0"}]
    assert dataset["suites"][0]["path"] == "suites/healthy.json"
    assert filtered == listed
    assert shown == dataset
    assert verification["passed"] is True
    assert verification["failed_check_count"] == 0
    assert {check["name"] for check in verification["checks"]} == {
        "dataset_root_exists",
        "dataset_manifest_exists",
        "dataset_manifest_matches_identity",
        "dataset_suites_load",
    }


@pytest.mark.asyncio
async def test_cli_eval_run_rejects_empty_scenario_selection() -> None:
    service, _ = build_cli_service([])
    output = StringIO()
    error = StringIO()

    status = await run_cli(
        ["eval", "run", "production-operator", "--kind", "recovery"],
        service=service,
        stdout=output,
        stderr=error,
    )

    assert status == 2
    assert output.getvalue() == ""
    assert "evaluation run requires at least one scenario" in error.getvalue()


@pytest.mark.asyncio
async def test_cli_eval_run_executes_suite_and_persists_report(tmp_path: Path) -> None:
    service, backend = build_cli_service([inspect_workload(), finish()])
    output = StringIO()
    report_dir = tmp_path / "reports"

    status = await run_cli(
        [
            "eval",
            "run",
            "production-operator",
            "--report-dir",
            str(report_dir),
            "--max-average-actions",
            "1.0",
            "--kind",
            "regression",
            "--tag",
            "smoke",
            "--exclude-tag",
            "slow",
        ],
        service=service,
        stdout=output,
    )
    payload = read_json(output)
    stored = FileEvaluationReportStore(report_dir).load("local evaluation suite")

    assert status == 0
    assert payload["passed"] is True
    assert payload["suite"]["summary"]["scenario_count"] == 1
    assert payload["suite"]["summary"]["action_started_count"] == 1
    scenario_payload = payload["suite"]["scenarios"][0]
    assert scenario_payload["kind"] == "regression"
    assert scenario_payload["tags"] == ["smoke", "kubernetes"]
    assert scenario_payload["evidence_claims"] == ["resource", "healthy", "kind", "relation:owns"]
    assert payload["gate"]["passed"] is True
    assert payload["report_dir"] == str(report_dir)
    assert stored.suite_name == "local evaluation suite"
    assert stored.scenarios[0].kind is not None
    assert stored.scenarios[0].kind.value == "regression"
    assert stored.scenarios[0].tags == ("smoke", "kubernetes")
    assert stored.scenarios[0].evidence_claims == (
        "resource",
        "healthy",
        "kind",
        "relation:owns",
    )
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_eval_run_can_emit_junit_xml() -> None:
    service, backend = build_cli_service([inspect_workload(), finish()])
    output = StringIO()

    status = await run_cli(
        [
            "eval",
            "run",
            "production-operator",
            "--kind",
            "regression",
            "--tag",
            "smoke",
            "--format",
            "junit",
        ],
        service=service,
        stdout=output,
    )
    root = fromstring(output.getvalue())

    assert status == 0
    assert root.tag == "testsuite"
    assert root.attrib["name"] == "local evaluation suite"
    assert root.attrib["tests"] == "2"
    assert root.attrib["failures"] == "0"
    assert [item.attrib["name"] for item in root.findall("testcase")] == [
        "healthy workload",
        "pass_rate",
    ]
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_eval_reports_lists_persisted_reports(tmp_path: Path) -> None:
    service, backend = build_cli_service([inspect_workload(), finish()])
    report_dir = tmp_path / "reports"
    run_output = StringIO()
    reports_output = StringIO()
    console_output = StringIO()
    text_console_output = StringIO()

    run_status = await run_cli(
        [
            "eval",
            "run",
            "production-operator",
            "--suite",
            "daily regression suite",
            "--report-dir",
            str(report_dir),
            "--kind",
            "regression",
        ],
        service=service,
        stdout=run_output,
    )
    reports_status = await run_cli(
        ["eval", "reports", "--report-dir", str(report_dir)],
        service=service,
        stdout=reports_output,
    )
    console_status = await run_cli(
        ["eval", "console", "--report-dir", str(report_dir)],
        service=service,
        stdout=console_output,
    )
    text_console_status = await run_cli(
        ["eval", "console", "--report-dir", str(report_dir), "--format", "text"],
        service=service,
        stdout=text_console_output,
    )
    payload = read_json(reports_output)
    console = console_output.getvalue()
    text_console = text_console_output.getvalue()
    reports = payload["reports"]
    assert isinstance(reports, list)
    report = reports[0]
    assert isinstance(report, dict)

    assert run_status == 0
    assert reports_status == 0
    assert console_status == 0
    assert text_console_status == 0
    assert payload["report_dir"] == str(report_dir)
    assert payload["report_count"] == 1
    assert report["suite_name"] == "daily regression suite"
    assert report["passed"] is True
    assert report["scenario_count"] == 1
    assert report["passed_count"] == 1
    assert report["failed_count"] == 0
    assert report["gate_passed"] is True
    assert report["failed_scenarios"] == []
    assert report["model_total_token_count"] == 0
    assert "Evaluation Console" in console
    assert "daily regression suite" in console
    assert "healthy workload" in console
    assert "Quality Gate Checks" in console
    assert "Universal Agent Evaluation Console" in text_console
    expected_summary = "Summary: suites=1 scenarios=1 passed=1 failed=0 gate_failures=0 tokens=0"
    assert expected_summary in text_console
    assert "- daily regression suite status=pass scenarios=1 passed=1 failed=0" in text_console
    assert "daily regression suite/healthy workload kind=regression" in text_console
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_eval_run_executes_suite_file_and_persists_report(tmp_path: Path) -> None:
    service, backend = build_cli_service([inspect_workload(), finish()])
    suite_path = tmp_path / "suite.json"
    report_dir = tmp_path / "reports"
    write_evaluation_suite_file(suite_path)
    output = StringIO()

    status = await run_cli(
        [
            "eval",
            "run",
            "production-operator",
            "--suite-file",
            str(suite_path),
            "--report-dir",
            str(report_dir),
            "--min-action-success-rate",
            "1.0",
        ],
        service=service,
        stdout=output,
    )
    payload = read_json(output)
    stored = FileEvaluationReportStore(report_dir).load("file evaluation suite")

    assert status == 0
    assert payload["passed"] is True
    assert payload["suite"]["suite_name"] == "file evaluation suite"
    assert payload["suite"]["scenarios"][0]["scenario_name"] == "file healthy workload"
    assert payload["gate"]["passed"] is True
    assert stored.suite_name == "file evaluation suite"
    assert stored.scenarios[0].scenario_name == "file healthy workload"
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_cli_eval_run_uses_suite_file_quality_gate_and_cli_overrides(
    tmp_path: Path,
) -> None:
    suite_path = tmp_path / "suite.json"
    write_evaluation_suite_file(
        suite_path,
        quality_gate={"max_average_actions_per_scenario": 0.0},
    )

    failing_output = StringIO()
    failing_status = await run_cli(
        [
            "eval",
            "run",
            "production-operator",
            "--suite-file",
            str(suite_path),
            "--fail-on-fail",
        ],
        service=build_cli_service([inspect_workload(), finish()])[0],
        stdout=failing_output,
    )
    failing_payload = read_json(failing_output)
    failed_checks = {
        item["name"]
        for item in failing_payload["gate"]["checks"]
        if isinstance(item, dict) and not item["passed"]
    }

    passing_output = StringIO()
    passing_status = await run_cli(
        [
            "eval",
            "run",
            "production-operator",
            "--suite-file",
            str(suite_path),
            "--max-average-actions",
            "1.0",
            "--fail-on-fail",
        ],
        service=build_cli_service([inspect_workload(), finish()])[0],
        stdout=passing_output,
    )
    passing_payload = read_json(passing_output)

    assert failing_status == 1
    assert failing_payload["passed"] is False
    assert "average_actions_per_scenario" in failed_checks
    assert passing_status == 0
    assert passing_payload["passed"] is True
    assert passing_payload["gate"]["passed"] is True


@pytest.mark.asyncio
async def test_cli_eval_run_can_fail_process_on_gate_failure() -> None:
    service, _ = build_cli_service([inspect_workload(), finish(), invalid_scale_workload()])
    output = StringIO()
    error = StringIO()

    status = await run_cli(
        [
            "eval",
            "run",
            "production-operator",
            "--max-average-actions",
            "0.0",
            "--fail-on-fail",
        ],
        service=service,
        stdout=output,
        stderr=error,
    )
    payload = read_json(output)

    assert status == 1
    assert error.getvalue() == ""
    assert payload["passed"] is False
    assert payload["gate"]["passed"] is False


@pytest.mark.asyncio
async def test_cli_eval_run_exposes_quality_gate_thresholds() -> None:
    service, _ = build_cli_service(
        [inspect_workload(), finish(), invalid_scale_workload()],
        usage=[
            ModelUsage(
                "scripted",
                "cli-gate-test",
                input_tokens=80,
                output_tokens=20,
                estimated_cost_micros=18,
            ),
            ModelUsage(
                "scripted",
                "cli-gate-test",
                input_tokens=40,
                output_tokens=10,
                estimated_cost_micros=9,
            ),
            ModelUsage("scripted", "cli-gate-test", input_tokens=10, output_tokens=5),
        ],
    )
    output = StringIO()
    error = StringIO()

    status = await run_cli(
        [
            "eval",
            "run",
            "production-operator",
            "--min-goal-completion-rate",
            "1.0",
            "--min-action-success-rate",
            "1.0",
            "--max-tool-failure-rate",
            "0.0",
            "--max-policy-denial-rate",
            "0.0",
            "--max-average-recoveries",
            "0.0",
            "--max-average-model-calls",
            "1.0",
            "--max-average-model-tokens",
            "1.0",
            "--max-total-model-cost-micros",
            "1",
            "--fail-on-fail",
        ],
        service=service,
        stdout=output,
        stderr=error,
    )
    payload = read_json(output)
    failed_checks = {
        item["name"]
        for item in payload["gate"]["checks"]
        if isinstance(item, dict) and not item["passed"]
    }

    assert status == 1
    assert error.getvalue() == ""
    assert payload["passed"] is False
    assert failed_checks >= {
        "goal_completion_rate",
        "policy_denial_rate",
        "average_model_calls_per_scenario",
        "average_model_tokens_per_scenario",
        "total_model_estimated_cost_micros",
    }


@pytest.mark.asyncio
async def test_cli_eval_compare_detects_report_drift(tmp_path: Path) -> None:
    service, _ = build_cli_service([inspect_workload(), finish(), invalid_scale_workload()])
    report_output = StringIO()
    report_dir = tmp_path / "reports"
    await run_cli(
        ["eval", "run", "production-operator", "--report-dir", str(report_dir)],
        service=service,
        stdout=report_output,
    )
    expected = FileEvaluationReportStore(report_dir).load("local evaluation suite")
    actual = replace(expected, summary=replace(expected.summary, action_started_count=2))
    expected_path = tmp_path / "expected.json"
    actual_path = tmp_path / "actual.json"
    expected_path.write_text(json.dumps(encode_evaluation_report(expected)), encoding="utf-8")
    actual_path.write_text(json.dumps(encode_evaluation_report(actual)), encoding="utf-8")
    output = StringIO()

    status = await run_cli(
        ["eval", "compare", str(expected_path), str(actual_path), "--fail-on-fail"],
        stdout=output,
    )
    payload = read_json(output)

    assert status == 1
    assert payload["passed"] is False
    assert "summary" in {
        item["name"] for item in payload["failed_checks"] if isinstance(item, dict)
    }


@pytest.mark.asyncio
async def test_cli_eval_replay_records_and_replays_golden_recording(tmp_path: Path) -> None:
    recording_dir = tmp_path / "replay-recordings"
    record_output = StringIO()

    record_status = await run_cli(
        [
            "eval",
            "replay",
            "production-operator",
            "--recording-dir",
            str(recording_dir),
            "--kind",
            "regression",
            "--update",
        ],
        service=build_cli_service([inspect_workload(), finish()])[0],
        stdout=record_output,
    )
    record_payload = read_json(record_output)
    stored = FileReplayRecordingStore(recording_dir).load("healthy workload")

    recordings_output = StringIO()
    recordings_status = await run_cli(
        ["eval", "recordings", "--recording-dir", str(recording_dir)],
        service=build_cli_service([inspect_workload(), finish()])[0],
        stdout=recordings_output,
    )
    recordings_payload = read_json(recordings_output)
    recordings = recordings_payload["recordings"]
    assert isinstance(recordings, list)
    recording = recordings[0]
    assert isinstance(recording, dict)

    replay_output = StringIO()
    replay_status = await run_cli(
        [
            "eval",
            "replay",
            "production-operator",
            "--recording-dir",
            str(recording_dir),
            "--kind",
            "regression",
            "--fail-on-fail",
        ],
        service=build_cli_service([inspect_workload(), finish()])[0],
        stdout=replay_output,
    )
    replay_payload = read_json(replay_output)

    assert record_status == 0
    assert record_payload["mode"] == "record"
    assert record_payload["passed"] is True
    assert record_payload["scenario_count"] == 1
    assert stored.scenario_name == "healthy workload"
    assert recordings_status == 0
    assert recordings_payload["recording_dir"] == str(recording_dir)
    assert recordings_payload["recording_count"] == 1
    assert recording["scenario_name"] == "healthy workload"
    assert recording["result_status"] == "completed"
    assert recording["error_code"] is None
    assert recording["action_started_count"] == 1
    assert recording["policy_denial_count"] == 0
    assert recording["action_capabilities"] == ["inspect_workload"]
    assert recording["policy_effects"] == ["allow"]
    assert replay_status == 0
    assert replay_payload["mode"] == "replay"
    assert replay_payload["passed"] is True
    assert replay_payload["scenarios"][0]["failed_checks"] == []


@pytest.mark.asyncio
async def test_cli_eval_replay_can_fail_process_on_drift(tmp_path: Path) -> None:
    recording_dir = tmp_path / "replay-recordings"
    await run_cli(
        [
            "eval",
            "replay",
            "production-operator",
            "--recording-dir",
            str(recording_dir),
            "--kind",
            "regression",
            "--update",
        ],
        service=build_cli_service([inspect_workload(), finish()])[0],
        stdout=StringIO(),
    )
    output = StringIO()
    error = StringIO()

    status = await run_cli(
        [
            "eval",
            "replay",
            "production-operator",
            "--recording-dir",
            str(recording_dir),
            "--kind",
            "regression",
            "--fail-on-fail",
        ],
        service=build_cli_service([invalid_scale_workload()])[0],
        stdout=output,
        stderr=error,
    )
    payload = read_json(output)

    assert status == 1
    assert error.getvalue() == ""
    assert payload["mode"] == "replay"
    assert payload["passed"] is False
    assert {item["name"] for item in payload["scenarios"][0]["failed_checks"]} >= {
        "result_status",
        "event_types",
    }
