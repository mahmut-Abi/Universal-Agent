from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from universal_agent import (
    AgentDelegationState,
    AgentDelegationTaskState,
    AgentId,
    AgentInstanceRecord,
    AgentInstanceStatus,
    AgentProfileRecord,
    AgentRegistry,
    AgentRuntime,
    AgentTaskId,
    Decision,
    DecisionType,
    DistributedLockOwnerId,
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
)
from universal_agent.agentd import AgentdApp, AgentdAuthPolicy, HttpRequest
from universal_agent.core import DomainIdentity, ExecutionStatus, JsonMapping, JsonValue, SessionId
from universal_agent.domain import (
    DomainPackage,
    DomainPackageCompatibility,
    DomainPackageManifest,
    DomainPackageRegistry,
)
from universal_agent.domains.kubernetes import KubernetesRemediationDomain
from universal_agent.evaluation.harness import EvaluationScenarioKind
from universal_agent.evaluation.recording import (
    EvaluationCheckRecording,
    EvaluationGateRecording,
    EvaluationReportRecording,
    EvaluationScenarioRecording,
    EvaluationSummaryRecording,
    FileEvaluationReportStore,
)
from universal_agent.security import EnvSecretProvider, SecretResolutionReport, resolve_secret_refs


class AgentdBackend:
    def __init__(self) -> None:
        self.inspect_calls = 0

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
        assert capability == "scale_workload"
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


class AgentdRemediationBackend:
    def __init__(self) -> None:
        self.inspect_calls: list[str] = []
        self.mutation_calls = 0
        self.scaled = False

    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        self.inspect_calls.append(capability)
        if capability == "inspect_workload":
            if not self.scaled:
                return immutable_json(
                    {
                        "resource": "deployment/example",
                        "healthy": False,
                        "desired_replicas": 3,
                        "ready_replicas": 1,
                    }
                )
            return immutable_json(
                {
                    "resource": "deployment/example",
                    "healthy": True,
                    "desired_replicas": 3,
                    "ready_replicas": 3,
                    "verification_observed": True,
                }
            )
        if capability == "inspect_pod":
            return immutable_json(
                {
                    "resource": "pod/example-123",
                    "root_cause": "under_replicated",
                }
            )
        raise AssertionError(f"unexpected inspection capability: {capability}")

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        assert capability == "scale_workload"
        self.mutation_calls += 1
        self.scaled = True
        return immutable_json({"resource": "deployment/example", "mutation_applied": True})


def inspect_workload(*observations: str) -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect workload",
        capability="inspect_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example"}),
        expected_observations=observations or ("healthy",),
    )


def inspect_pod() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Inspect pod",
        capability="inspect_pod",
        target="pod/example-123",
        arguments=immutable_json({"name": "example-123"}),
        expected_observations=("root_cause",),
    )


def scale_workload() -> Decision:
    return Decision(
        DecisionType.EXECUTE,
        "Scale workload",
        capability="scale_workload",
        target="deployment/example",
        arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 3}),
        expected_observations=("mutation_applied",),
    )


def finish() -> Decision:
    return Decision(DecisionType.FINISH, "Required evidence is present")


def wait() -> Decision:
    return Decision(DecisionType.WAIT, "Operator pause requested")


def goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Verify workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ("healthy",)),
    )


def goal_submission_body(
    *,
    goal_description: str = "Verify workload health",
    task_description: str = "Inspect workload",
    required_criteria: tuple[str, ...] = ("healthy",),
    profile: str | None = None,
) -> JsonMapping:
    success_criterion: dict[str, JsonValue] = {"key": "healthy", "expected": True}
    criteria: list[JsonValue] = [success_criterion]
    required: list[JsonValue] = list(required_criteria)
    goal_payload: dict[str, JsonValue] = {
        "description": goal_description,
        "success_criteria": criteria,
    }
    task_payload: dict[str, JsonValue] = {
        "description": task_description,
        "required_criteria": required,
    }
    body: dict[str, JsonValue] = {"goal": goal_payload, "task": task_payload}
    if profile is not None:
        body["profile"] = profile
    return immutable_json(body)


def json_object(value: JsonValue) -> JsonMapping:
    assert isinstance(value, dict)
    return value


def json_array(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def json_string(value: JsonValue) -> str:
    assert isinstance(value, str)
    return value


def evaluation_report(suite_name: str) -> EvaluationReportRecording:
    return EvaluationReportRecording(
        suite_name,
        True,
        EvaluationSummaryRecording(
            scenario_count=1,
            passed_count=1,
            failed_count=0,
            goal_completed_count=1,
            task_completed_count=1,
            action_started_count=1,
            action_completed_count=1,
            tool_failure_count=0,
            policy_denial_count=0,
            recovery_planned_count=0,
            human_intervention_count=0,
            execution_duration_ms=42,
            model_call_count=1,
            model_total_token_count=123,
            model_estimated_cost_micros=7,
        ),
        (
            EvaluationScenarioRecording(
                "healthy workload",
                True,
                ExecutionStatus.COMPLETED,
                None,
                kind=EvaluationScenarioKind.REGRESSION,
                tags=("smoke", "kubernetes"),
                satisfied_criteria=immutable_json({"healthy": True}),
                checks=(EvaluationCheckRecording("status", True, "matched"),),
                event_types=("ActionStarted", "GoalCompleted", "EvaluationCompleted"),
                action_capabilities=("inspect_workload",),
                evidence_claims=("healthy",),
            ),
        ),
        EvaluationGateRecording(
            True,
            (EvaluationCheckRecording("min_pass_rate", True, "matched"),),
        ),
    )


def build_service(
    decisions: list[Decision],
    *,
    usage: list[ModelUsage] | None = None,
    distributed_coordinator: DistributedRuntimeCoordinator | None = None,
    domain_packages: DomainPackageRegistry | None = None,
    agent_registry: AgentRegistry | None = None,
    agent_delegation_state: AgentDelegationState | None = None,
    environment: JsonMapping | None = None,
    secrets: tuple[SecretRef, ...] = (),
    secret_resolution: SecretResolutionReport | None = None,
    model_config: ModelConfig | None = None,
) -> tuple[RuntimeService, AgentdBackend]:
    backend = AgentdBackend()
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    environment = (
        environment if environment is not None else immutable_json({"environment": "staging"})
    )
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions, usage=usage or ()),
        state_store=store,
        components=components,
        event_sink=events,
        environment=environment,
    )
    api = RuntimeAPI(runtime=runtime, session_store=store, event_reader=events)
    config = RuntimeConfig(
        environment=environment,
        secrets=secrets,
        model=model_config or ModelConfig.scripted(),
        store=StoreConfig.memory(),
        limits=RuntimeLimitsConfig(max_iterations=12, max_recovery_steps=4),
        domain=DomainConfig("kubernetes", "0.2.0"),
    )
    return RuntimeService(
        runtime_api=api,
        components=components,
        config=config,
        secret_resolution=secret_resolution,
        distributed_coordinator=distributed_coordinator,
        domain_packages=domain_packages,
        agent_registry=agent_registry,
        agent_delegation_state=agent_delegation_state,
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
            resources=("resources/runbook.md", "schemas/workload.json"),
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


def build_profile_service(
    decisions: list[Decision],
    *,
    profile_name: str = "production-operator",
    profile_domain: DomainIdentity | None = None,
) -> tuple[RuntimeService, AgentdBackend]:
    profile_domain = profile_domain or DomainIdentity("kubernetes", "0.2.0")
    backend = AgentdBackend()
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "production"}),
    )
    api = RuntimeAPI(runtime=runtime, session_store=store, event_reader=events)
    profile = ProfileConfig.from_mapping(
        {
            "name": profile_name,
            "version": "1.0.0",
            "description": "Production Kubernetes operator",
            "domain": {"name": profile_domain.name, "version": profile_domain.version},
        }
    ).to_profile()
    return (
        RuntimeService(
            runtime_api=api,
            components=components,
            profiles=(profile,),
        ),
        backend,
    )


def remediation_goal_task() -> tuple[Goal, Task]:
    return (
        Goal("Restore workload health", (SuccessCriterion("healthy", True),)),
        Task("Inspect workload", ()),
    )


def build_remediation_service(
    decisions: list[Decision],
    *,
    distributed_coordinator: DistributedRuntimeCoordinator | None = None,
) -> tuple[RuntimeService, AgentdRemediationBackend]:
    backend = AgentdRemediationBackend()
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    runtime = AgentRuntime(
        model=ScriptedModelAdapter(decisions),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "production"}),
    )
    api = RuntimeAPI(runtime=runtime, session_store=store, event_reader=events)
    return (
        RuntimeService(
            runtime_api=api,
            components=components,
            distributed_coordinator=distributed_coordinator,
        ),
        backend,
    )


def find_named(items: JsonValue, name: str) -> dict[str, JsonValue]:
    assert isinstance(items, list)
    for item in items:
        assert isinstance(item, dict)
        if item["name"] == name:
            return item
    raise AssertionError(f"missing item: {name}")


@pytest.mark.asyncio
async def test_agentd_config_route_redacts_sensitive_environment_values() -> None:
    service, _ = build_service(
        [],
        environment=immutable_json(
            {
                "environment": "staging",
                "password": "pw-value",
                "nested": {"api_token": "token-value", "safe": "visible"},
            }
        ),
    )
    app = AgentdApp(service)

    config = await app.handle(HttpRequest("GET", "/v1/config"))

    assert config.status_code == 200
    assert config.body is not None
    environment = config.body["environment"]
    assert isinstance(environment, dict)
    assert environment == {
        "environment": "staging",
        "password": "<redacted>",
        "nested": {"api_token": "<redacted>", "safe": "visible"},
    }
    assert config.body["secrets"] == []


@pytest.mark.asyncio
async def test_agentd_config_route_exposes_secret_references_without_values() -> None:
    service, _ = build_service(
        [],
        secrets=(SecretRef.env("openai_api_key", "OPENAI_API_KEY"),),
    )
    app = AgentdApp(service)

    config = await app.handle(HttpRequest("GET", "/v1/config"))

    assert config.status_code == 200
    assert config.body["secrets"] == [
        {
            "name": "openai_api_key",
            "source": "env",
            "key": "OPENAI_API_KEY",
            "required": True,
        }
    ]


@pytest.mark.asyncio
async def test_agentd_config_route_exposes_secret_status_without_values() -> None:
    secrets = (SecretRef.env("openai_api_key", "OPENAI_API_KEY"),)
    service, _ = build_service(
        [],
        secrets=secrets,
        secret_resolution=resolve_secret_refs(
            secrets,
            provider=EnvSecretProvider({"OPENAI_API_KEY": "secret-value"}),
        ),
    )
    app = AgentdApp(service)

    config = await app.handle(HttpRequest("GET", "/v1/config"))

    assert config.status_code == 200
    assert config.body["secrets"] == [
        {
            "name": "openai_api_key",
            "source": "env",
            "key": "OPENAI_API_KEY",
            "required": True,
            "available": True,
            "status": "available",
        }
    ]
    assert "secret-value" not in str(config.body)


@pytest.mark.asyncio
async def test_agentd_config_route_exposes_model_config_without_secret_values() -> None:
    service, _ = build_service(
        [],
        secrets=(SecretRef.env("openai_api_key", "OPENAI_API_KEY"),),
        secret_resolution=resolve_secret_refs(
            (SecretRef.env("openai_api_key", "OPENAI_API_KEY"),),
            provider=EnvSecretProvider({"OPENAI_API_KEY": "secret-value"}),
        ),
        model_config=ModelConfig.json_http(
            name="runtime-decider",
            endpoint="https://models.example.test/decide",
            api_key_secret="openai_api_key",
            timeout_seconds=4.5,
            headers={"X-Agent-Runtime": "agentd-test"},
        ),
    )
    app = AgentdApp(service)

    config = await app.handle(HttpRequest("GET", "/v1/config"))

    assert config.status_code == 200
    assert config.body["model"] == {
        "provider": "json_http",
        "name": "runtime-decider",
        "endpoint": "https://models.example.test/decide",
        "api_key_secret": "openai_api_key",
        "timeout_seconds": 4.5,
        "headers": {"X-Agent-Runtime": "agentd-test"},
    }
    assert "secret-value" not in str(config.body)


@pytest.mark.asyncio
async def test_agentd_auth_policy_protects_non_public_routes() -> None:
    service, _ = build_service([])
    app = AgentdApp(
        service,
        auth=AgentdAuthPolicy(
            bearer_token="local-token",
            read_only_bearer_token="reader-token",
        ),
    )

    health = await app.handle(HttpRequest("GET", "/health"))
    missing = await app.handle(HttpRequest("GET", "/v1/config"))
    invalid = await app.handle(
        HttpRequest(
            "GET",
            "/v1/config",
            headers={"authorization": "Bearer wrong-token"},
        )
    )
    authorized = await app.handle(
        HttpRequest(
            "GET",
            "/v1/config",
            headers={"AUTHORIZATION": "Bearer local-token"},
        )
    )
    read_allowed = await app.handle(
        HttpRequest(
            "GET",
            "/v1/config",
            headers={"authorization": "Bearer reader-token"},
        )
    )
    read_denied = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions",
            headers={"authorization": "Bearer reader-token"},
        )
    )

    assert health.status_code == 200
    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert missing.body["error"] == {
        "code": "unauthorized",
        "message": "authentication required",
    }
    assert missing.headers["www-authenticate"] == 'Bearer realm="agentd"'
    assert authorized.status_code == 200
    assert read_allowed.status_code == 200
    assert read_denied.status_code == 403
    assert read_denied.body["error"] == {
        "code": "forbidden",
        "message": "insufficient bearer token scope",
    }


def test_agentd_auth_policy_rejects_ambiguous_token_scopes() -> None:
    with pytest.raises(ValueError, match="must differ"):
        AgentdAuthPolicy(
            bearer_token="same-token",
            read_only_bearer_token="same-token",
        )


@pytest.mark.asyncio
async def test_agentd_catalog_routes_expose_runtime_service_views() -> None:
    service, _ = build_service([])
    app = AgentdApp(service)

    health = await app.handle(HttpRequest("GET", "/health"))
    ready = await app.handle(HttpRequest("GET", "/ready"))
    domains = await app.handle(HttpRequest("GET", "/v1/domains"))
    capabilities = await app.handle(HttpRequest("GET", "/v1/capabilities"))
    tools = await app.handle(HttpRequest("GET", "/v1/tools"))
    policies = await app.handle(HttpRequest("GET", "/v1/policies"))
    evaluators = await app.handle(HttpRequest("GET", "/v1/evaluators"))
    memory = await app.handle(HttpRequest("GET", "/v1/memory"))
    config = await app.handle(HttpRequest("GET", "/v1/config"))

    assert health.status_code == 200
    assert health.body["status"] == "ok"
    assert health.headers["content-type"] == "application/json"
    assert ready.body["ready"] is True
    assert ready.body["capability_count"] == 6
    assert domains.body["domains"] == [
        {
            "name": "kubernetes",
            "version": "0.2.0",
            "description": "Kubernetes inspection with policy-gated workload remediation",
            "primary": True,
            "ontology": ["Cluster", "Node", "Namespace", "Pod", "Deployment", "Service"],
            "capability_names": [
                "inspect_cluster",
                "inspect_workload",
                "inspect_pod",
                "inspect_logs",
                "inspect_events",
                "scale_workload",
            ],
            "evaluator_names": ["workload-health"],
        }
    ]

    scale = find_named(capabilities.body["capabilities"], "scale_workload")
    assert scale["risk"] == "medium"
    assert scale["tool_names"] == ["kubernetes_scale_workload"]
    assert scale["required_arguments"] == ["name", "namespace", "replicas"]
    scale_schema = scale["argument_schema"]
    assert isinstance(scale_schema, dict)

    scale_tool = find_named(tools.body["tools"], "kubernetes_scale_workload")
    assert scale_tool["side_effect"] == "reversible"
    assert scale_tool["required_arguments"] == ["name", "namespace", "replicas"]
    argument_schema = scale_tool["argument_schema"]
    assert isinstance(argument_schema, dict)
    schema_properties = argument_schema["properties"]
    assert isinstance(schema_properties, dict)
    replicas_schema = schema_properties["replicas"]
    assert isinstance(replicas_schema, dict)
    assert replicas_schema["type"] == "integer"
    assert replicas_schema["minimum"] == 0
    current_replicas_schema = schema_properties["current_replicas"]
    assert isinstance(current_replicas_schema, dict)
    assert current_replicas_schema["type"] == "integer"
    assert current_replicas_schema["minimum"] == 0
    resource_version_schema = schema_properties["resource_version"]
    assert isinstance(resource_version_schema, dict)
    assert resource_version_schema["type"] == ["string", "integer"]

    scale_policy = find_named(policies.body["policies"], "kubernetes-scale-safety")
    assert scale_policy["policy_type"] == "KubernetesScalePolicy"
    assert scale_policy["effect"] is None

    evaluator = find_named(evaluators.body["evaluators"], "workload-health")
    assert evaluator["evaluator_type"] == "WorkloadHealthEvaluator"

    memories = memory.body["memories"]
    assert isinstance(memories, list)
    assert {item["subject"] for item in memories if isinstance(item, dict)} >= {
        "kubernetes readiness",
        "unhealthy workload triage",
    }
    assert config.status_code == 200
    assert config.body == {
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
async def test_agentd_domain_package_routes_expose_read_only_catalog() -> None:
    service, _ = build_service([], domain_packages=package_registry())
    app = AgentdApp(service)

    listed = await app.handle(HttpRequest("GET", "/v1/domain-packages"))
    filtered = await app.handle(HttpRequest("GET", "/v1/domain-packages?tag=ops"))
    missing_filter = await app.handle(HttpRequest("GET", "/v1/domain-packages?tag=database"))
    detail = await app.handle(HttpRequest("GET", "/v1/domain-packages/kubernetes/0.2.0"))
    missing = await app.handle(HttpRequest("GET", "/v1/domain-packages/database"))
    wrong_method = await app.handle(HttpRequest("POST", "/v1/domain-packages"))

    assert listed.status_code == 200
    packages = json_array(listed.body["domain_packages"])
    assert len(packages) == 1
    package = json_object(packages[0])
    assert package["name"] == "kubernetes"
    assert package["version"] == "0.2.0"
    assert package["entrypoint"] == "universal_agent.domains.kubernetes:KubernetesRemediationDomain"
    assert package["capability_names"] == ["inspect_workload", "scale_workload"]
    assert package["dependencies"] == [{"name": "observability", "version": "1.0.0"}]
    assert package["resource_names"] == ["resources/runbook.md", "schemas/workload.json"]
    assert package["compatibility"] == {
        "runtime_api": ">=0.1,<1",
        "domain_api": "agent.nantian.dev/v1alpha1",
    }
    assert package["security"] == {"side_effects": "reversible"}
    assert filtered.body == listed.body
    assert missing_filter.body == {"domain_packages": []}
    assert detail.status_code == 200
    assert detail.body == package
    assert missing.status_code == 404
    assert missing.body["error"] == {
        "code": "not_found",
        "message": "domain package not registered: database",
    }
    assert wrong_method.status_code == 405


@pytest.mark.asyncio
async def test_agentd_state_event_repair_route_requires_confirmation_and_reports_clean() -> None:
    service, _ = build_service([])
    app = AgentdApp(service)

    repaired = await app.handle(
        HttpRequest(
            "POST",
            "/v1/doctor/state-events/repair",
            immutable_json({"confirmed": True}),
        )
    )
    rejected = await app.handle(
        HttpRequest(
            "POST",
            "/v1/doctor/state-events/repair",
            immutable_json({"confirmed": False}),
        )
    )
    dry_run = await app.handle(
        HttpRequest(
            "POST",
            "/v1/doctor/state-events/repair",
            immutable_json({"dry_run": True}),
        )
    )
    invalid_confirmed = await app.handle(
        HttpRequest(
            "POST",
            "/v1/doctor/state-events/repair",
            immutable_json({"confirmed": "true"}),
        )
    )
    invalid_dry_run = await app.handle(
        HttpRequest(
            "POST",
            "/v1/doctor/state-events/repair",
            immutable_json({"dry_run": "false"}),
        )
    )
    wrong_method = await app.handle(HttpRequest("GET", "/v1/doctor/state-events/repair"))

    assert repaired.status_code == 200
    assert repaired.body["status"] == "clean"
    assert repaired.body["repaired_event_count"] == 0
    assert repaired.body["skipped_item_count"] == 0
    assert dry_run.status_code == 200
    assert dry_run.body["status"] == "clean"
    assert rejected.status_code == 400
    assert "confirmed=true" in json_string(json_object(rejected.body["error"])["message"])
    assert invalid_confirmed.status_code == 400
    assert invalid_confirmed.body["error"] == {
        "code": "bad_request",
        "message": "state/event repair confirmed must be a boolean",
    }
    assert invalid_dry_run.status_code == 400
    assert invalid_dry_run.body["error"] == {
        "code": "bad_request",
        "message": "state/event repair dry_run must be a boolean",
    }
    assert wrong_method.status_code == 405


@pytest.mark.asyncio
async def test_agentd_profile_route_exposes_profile_catalog() -> None:
    service, _ = build_profile_service([])
    app = AgentdApp(service)

    response = await app.handle(HttpRequest("GET", "/v1/profiles"))

    assert response.status_code == 200
    assert response.body["profiles"] == [
        {
            "name": "production-operator",
            "version": "1.0.0",
            "description": "Production Kubernetes operator",
            "domain_name": "kubernetes",
            "domain_version": "0.2.0",
            "domains": [{"name": "kubernetes", "version": "0.2.0"}],
        }
    ]


@pytest.mark.asyncio
async def test_agentd_multi_agent_route_exposes_optional_registry_projection() -> None:
    registry = AgentRegistry(
        profiles=(
            AgentProfileRecord(
                "security-auditor",
                "1.0.0",
                (DomainIdentity("kubernetes", "0.2.0"),),
                permissions=("read_only", "security_review"),
                capabilities=("inspect_workload",),
                description="Read-only security checks",
            ),
        ),
        instances=(
            AgentInstanceRecord(
                AgentId("agent-1"),
                "security-auditor",
                "1.0.0",
                status=AgentInstanceStatus.READY,
            ),
        ),
    )
    service, _ = build_service(
        [],
        agent_registry=registry,
        agent_delegation_state=AgentDelegationState(
            (AgentDelegationTaskState(AgentTaskId("parent-task"), 1, 0),)
        ),
    )
    app = AgentdApp(service)

    response = await app.handle(HttpRequest("GET", "/v1/multi-agent"))

    assert response.status_code == 200
    assert response.body["enabled"] is True
    assert response.body["profile_count"] == 1
    assert response.body["instance_count"] == 1
    assert response.body["ready_instance_count"] == 1
    profiles = json_array(response.body["profiles"])
    instances = json_array(response.body["instances"])
    delegation_tasks = json_array(response.body["delegation_tasks"])
    assert json_object(profiles[0])["permissions"] == ["read_only", "security_review"]
    assert json_object(instances[0])["agent_id"] == "agent-1"
    assert delegation_tasks == [{"task_id": "parent-task", "child_count": 1, "delegation_depth": 0}]


@pytest.mark.asyncio
async def test_agentd_profile_show_route_exposes_one_profile() -> None:
    service, _ = build_profile_service([])
    app = AgentdApp(service)

    response = await app.handle(HttpRequest("GET", "/v1/profiles/production-operator"))

    assert response.status_code == 200
    assert response.body == {
        "name": "production-operator",
        "version": "1.0.0",
        "description": "Production Kubernetes operator",
        "domain_name": "kubernetes",
        "domain_version": "0.2.0",
        "domains": [{"name": "kubernetes", "version": "0.2.0"}],
    }


@pytest.mark.asyncio
async def test_agentd_profile_show_route_returns_404_for_unknown_profile() -> None:
    service, _ = build_profile_service([])
    app = AgentdApp(service)

    response = await app.handle(HttpRequest("GET", "/v1/profiles/missing-profile"))

    assert response.status_code == 404
    assert response.body["error"] == {
        "code": "not_found",
        "message": "profile not found: missing-profile",
    }


@pytest.mark.asyncio
async def test_agentd_create_session_route_accepts_configured_profile() -> None:
    service, backend = build_profile_service([inspect_workload(), finish()])
    app = AgentdApp(service)

    created = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions",
            goal_submission_body(profile="production-operator"),
        )
    )

    assert created.status_code == 201
    result = created.body["result"]
    assert isinstance(result, dict)
    assert result["status"] == "completed"
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_agentd_create_session_route_rejects_unknown_profile() -> None:
    service, _ = build_profile_service([])
    app = AgentdApp(service)

    response = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions",
            goal_submission_body(profile="missing-profile"),
        )
    )

    assert response.status_code == 400
    assert response.body["error"] == {
        "code": "bad_request",
        "message": "unknown profile: missing-profile",
    }


@pytest.mark.asyncio
async def test_agentd_create_session_route_rejects_unbound_profile() -> None:
    service, backend = build_profile_service(
        [inspect_workload(), finish()],
        profile_name="observability-operator",
        profile_domain=DomainIdentity("observability", "1.0.0"),
    )
    app = AgentdApp(service)

    response = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions",
            goal_submission_body(profile="observability-operator"),
        )
    )

    assert response.status_code == 400
    assert response.body["error"] == {
        "code": "bad_request",
        "message": (
            "profile observability-operator is not bound to this RuntimeService: "
            "profile domains observability@1.0.0 do not match active runtime domains "
            "kubernetes@0.2.0"
        ),
    }
    assert backend.inspect_calls == 0


@pytest.mark.asyncio
async def test_agentd_create_session_route_runs_goal_and_exposes_session_events() -> None:
    service, backend = build_service([inspect_workload(), finish()])
    app = AgentdApp(service)

    created = await app.handle(HttpRequest("POST", "/v1/sessions", goal_submission_body()))

    assert created.status_code == 201
    result = created.body["result"]
    session = created.body["session"]
    assert isinstance(result, dict)
    assert isinstance(session, dict)
    assert result["status"] == "completed"
    assert session["goal_status"] == "completed"
    session_id = result["session_id"]
    assert isinstance(session_id, str)

    fetched = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}"))
    listed = await app.handle(HttpRequest("GET", "/v1/sessions"))
    events = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/events"))
    diagnostics = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/diagnostics"))
    evidence = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/evidence"))
    world = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/world"))
    world_neighborhood = await app.handle(
        HttpRequest(
            "GET",
            f"/v1/sessions/{session_id}/world?entity_id=deployment/example&relation=owns",
        )
    )

    assert fetched.status_code == 200
    assert fetched.body["session_id"] == session_id
    assert listed.status_code == 200
    session_items = listed.body["sessions"]
    assert isinstance(session_items, list)
    assert len(session_items) == 1
    listed_session = session_items[0]
    assert isinstance(listed_session, dict)
    assert listed_session["session_id"] == session_id
    assert listed_session["goal_description"] == "Verify workload health"
    assert listed_session["goal_status"] == "completed"
    assert listed_session["current_task_status"] == "completed"
    assert listed_session["pending_action"] is False
    assert listed_session["domain_name"] == "kubernetes"
    assert events.status_code == 200
    assert diagnostics.status_code == 200
    assert evidence.status_code == 200
    assert world.status_code == 200
    assert world_neighborhood.status_code == 200
    assert evidence.body["session_id"] == session_id
    assert world.body["session_id"] == session_id
    evidence_items = diagnostics.body["evidence"]
    world_items = diagnostics.body["world_facts"]
    assert isinstance(evidence_items, list)
    assert isinstance(world_items, list)
    evidence_claims = {item["claim"]: item for item in evidence_items if isinstance(item, dict)}
    world_claims = {item["claim"]: item for item in world_items if isinstance(item, dict)}
    assert evidence_claims["healthy"]["value"] is True
    assert world_claims["healthy"]["value"] is True
    assert evidence.body["evidence"] == evidence_items
    assert world.body["world_facts"] == world_items
    history_items = world.body["world_fact_histories"]
    assert isinstance(history_items, list)
    healthy_history = next(
        item for item in history_items if isinstance(item, dict) and item.get("claim") == "healthy"
    )
    assert healthy_history["conflicting"] is False
    current_history = healthy_history["current"]
    candidates = healthy_history["candidates"]
    assert isinstance(current_history, dict)
    assert isinstance(candidates, list)
    assert current_history["value"] is True
    assert len(candidates) == 1
    assert world.body["world_entities"] == diagnostics.body["world_entities"]
    assert world.body["world_relations"] == diagnostics.body["world_relations"]
    world_entities = diagnostics.body["world_entities"]
    world_relations = diagnostics.body["world_relations"]
    assert isinstance(world_entities, list)
    assert isinstance(world_relations, list)
    first_entity = cast(dict[str, JsonValue], world_entities[0])
    first_relation = cast(dict[str, JsonValue], world_relations[0])
    attributes = first_entity["attributes"]
    assert isinstance(attributes, dict)
    assert first_entity["entity_id"] == "deployment/example"
    assert first_entity["kind"] == "Deployment"
    assert attributes["healthy"] is True
    assert first_relation["source"] == "deployment/example"
    assert first_relation["relation"] == "owns"
    assert first_relation["target"] == "pod/example-1"
    neighborhood = world_neighborhood.body["neighborhood"]
    assert isinstance(neighborhood, dict)
    root = neighborhood["root"]
    outgoing_relations = neighborhood["outgoing_relations"]
    assert isinstance(root, dict)
    assert isinstance(outgoing_relations, list)
    first_outgoing = cast(dict[str, JsonValue], outgoing_relations[0])
    assert root["entity_id"] == "deployment/example"
    assert first_outgoing["relation"] == "owns"
    assert first_outgoing["target"] == "pod/example-1"
    event_items = events.body["events"]
    assert isinstance(event_items, list)
    last_event = event_items[-1]
    assert isinstance(last_event, dict)
    assert last_event["type"] == "GoalCompleted"
    assert events.body["next_cursor"] == last_event["event_id"]
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_agentd_web_console_route_renders_runtime_snapshot() -> None:
    service, backend = build_service(
        [inspect_workload(), finish()],
        domain_packages=package_registry(),
    )
    app = AgentdApp(service)

    created = await app.handle(HttpRequest("POST", "/v1/sessions", goal_submission_body()))
    result = created.body["result"]
    assert isinstance(result, dict)
    session_id = result["session_id"]
    assert isinstance(session_id, str)

    console = await app.handle(
        HttpRequest("GET", f"/console?session_id={session_id}&event_limit=20")
    )
    sessions_page = await app.handle(HttpRequest("GET", "/console/sessions?event_limit=20"))
    detail = await app.handle(HttpRequest("GET", f"/console/sessions/{session_id}?event_limit=20"))
    evidence_page = await app.handle(HttpRequest("GET", f"/console/sessions/{session_id}/evidence"))
    world_page = await app.handle(HttpRequest("GET", f"/console/sessions/{session_id}/world"))
    focused_world_page = await app.handle(
        HttpRequest(
            "GET",
            f"/console/sessions/{session_id}/world?entity_id=deployment/example",
        )
    )
    top_evidence_page = await app.handle(
        HttpRequest("GET", f"/console/evidence?session_id={session_id}")
    )
    top_world_page = await app.handle(HttpRequest("GET", f"/console/world?session_id={session_id}"))
    top_focused_world_page = await app.handle(
        HttpRequest(
            "GET",
            f"/console/world?session_id={session_id}&entity_id=deployment/example&relation=owns",
        )
    )
    domain_page = await app.handle(HttpRequest("GET", "/console/domains/kubernetes/0.2.0"))
    package_page = await app.handle(HttpRequest("GET", "/console/domain-packages/kubernetes/0.2.0"))
    profile_page = await app.handle(HttpRequest("GET", "/console/profiles"))
    doctor_page = await app.handle(HttpRequest("GET", "/console/doctor"))
    distributed_page = await app.handle(HttpRequest("GET", "/console/distributed"))
    multi_agent_page = await app.handle(HttpRequest("GET", "/console/multi-agent"))
    settings_page = await app.handle(HttpRequest("GET", "/console/settings"))
    catalog_pages = {
        "domains": "Domain Catalog",
        "domain-packages": "Domain Package Catalog",
        "capabilities": "Capability Catalog",
        "tools": "Tool Catalog",
        "policies": "Policy Catalog",
        "evaluators": "Evaluator Catalog",
        "memory": "Memory Catalog",
    }
    catalog_responses = {
        name: await app.handle(HttpRequest("GET", f"/console/{name}")) for name in catalog_pages
    }
    catalog_method = await app.handle(HttpRequest("POST", "/console/tools"))
    missing_domain_page = await app.handle(HttpRequest("GET", "/console/domains/missing/0.1.0"))
    missing_package_page = await app.handle(
        HttpRequest("GET", "/console/domain-packages/missing/0.1.0")
    )
    package_method = await app.handle(
        HttpRequest("POST", "/console/domain-packages/kubernetes/0.2.0")
    )
    unknown_detail_page = await app.handle(
        HttpRequest("GET", f"/console/sessions/{session_id}/unknown")
    )
    missing = await app.handle(HttpRequest("GET", "/console?session_id=missing-session"))
    missing_detail = await app.handle(HttpRequest("GET", "/console/sessions/missing-session"))
    invalid_limit = await app.handle(HttpRequest("GET", "/console?event_limit=0"))
    invalid_detail_limit = await app.handle(
        HttpRequest("GET", f"/console/sessions/{session_id}?event_limit=0")
    )
    invalid_world_filter = await app.handle(
        HttpRequest("GET", f"/console/sessions/{session_id}/world?relation=owns")
    )

    assert console.status_code == 200
    assert console.headers["content-type"] == "text/html; charset=utf-8"
    assert console.text_body is not None
    assert "Universal Agent Runtime Console" in console.text_body
    assert "Runtime Console" in console.text_body
    assert "Verify workload health" in console.text_body
    assert "kubernetes@0.2.0" in console.text_body
    assert "Domain Package Catalog" in console.text_body
    assert "universal_agent.domains.kubernetes:KubernetesRemediationDomain" in console.text_body
    assert "Capability Catalog" in console.text_body
    assert "inspect_workload" in console.text_body
    assert "Tool Catalog" in console.text_body
    assert "kubernetes_inspect_workload" in console.text_body
    assert "ActionStarted" in console.text_body
    assert "capability=inspect_workload" in console.text_body
    assert sessions_page.status_code == 200
    assert sessions_page.text_body is not None
    assert "Universal Agent Runtime Sessions" in sessions_page.text_body
    assert "Verify workload health" in sessions_page.text_body
    assert "Selected Session" in sessions_page.text_body
    assert detail.status_code == 200
    assert detail.headers["content-type"] == "text/html; charset=utf-8"
    assert detail.text_body is not None
    assert "Universal Agent Runtime Session Detail" in detail.text_body
    assert "Session Detail" in detail.text_body
    assert f"session={session_id}" in detail.text_body
    assert "Task Timeline" in detail.text_body
    assert "World Facts" in detail.text_body
    assert "Session Evidence" in detail.text_body
    assert "ActionStarted" in detail.text_body
    assert "capability=inspect_workload" in detail.text_body
    assert evidence_page.status_code == 200
    assert evidence_page.text_body is not None
    assert "Universal Agent Runtime Evidence Explorer" in evidence_page.text_body
    assert "Session Evidence" in evidence_page.text_body
    assert "deployment/example" in evidence_page.text_body
    assert world_page.status_code == 200
    assert world_page.text_body is not None
    assert "Universal Agent Runtime World Model Explorer" in world_page.text_body
    assert "World Facts" in world_page.text_body
    assert "healthy" in world_page.text_body
    assert "No focused world neighborhood selected" in world_page.text_body
    assert focused_world_page.status_code == 200
    assert focused_world_page.text_body is not None
    assert "Focused World Neighborhood" in focused_world_page.text_body
    assert "deployment/example" in focused_world_page.text_body
    assert "pod/example-1" in focused_world_page.text_body
    assert top_evidence_page.status_code == 200
    assert top_evidence_page.text_body is not None
    assert "Universal Agent Runtime Evidence Explorer" in top_evidence_page.text_body
    assert "deployment/example" in top_evidence_page.text_body
    assert top_world_page.status_code == 200
    assert top_world_page.text_body is not None
    assert "Universal Agent Runtime World Model Explorer" in top_world_page.text_body
    assert "healthy" in top_world_page.text_body
    assert top_focused_world_page.status_code == 200
    assert top_focused_world_page.text_body is not None
    assert "Focused World Neighborhood" in top_focused_world_page.text_body
    assert "No incoming focused relations" in top_focused_world_page.text_body
    assert domain_page.status_code == 200
    assert domain_page.text_body is not None
    assert "Universal Agent Runtime Domain Manager" in domain_page.text_body
    assert "Domain Manager" in domain_page.text_body
    assert "domain=kubernetes@0.2.0" in domain_page.text_body
    assert "inspect_workload" in domain_page.text_body
    assert "kubernetes_inspect_workload" in domain_page.text_body
    assert package_page.status_code == 200
    assert package_page.text_body is not None
    assert "Universal Agent Runtime Domain Package" in package_page.text_body
    assert "package=kubernetes@0.2.0" in package_page.text_body
    assert (
        "universal_agent.domains.kubernetes:KubernetesRemediationDomain" in package_page.text_body
    )
    assert "Package Resources" in package_page.text_body
    assert "resources/runbook.md" in package_page.text_body
    assert "Package Security" in package_page.text_body
    assert "reversible" in package_page.text_body
    assert "Matching Active Domains" in package_page.text_body
    assert profile_page.status_code == 200
    assert profile_page.text_body is not None
    assert "Universal Agent Runtime Profile Catalog" in profile_page.text_body
    assert "Profile Catalog" in profile_page.text_body
    assert "Active Domains" in profile_page.text_body
    assert doctor_page.status_code == 200
    assert doctor_page.headers["content-type"] == "text/html; charset=utf-8"
    assert doctor_page.text_body is not None
    assert "Universal Agent Runtime Doctor" in doctor_page.text_body
    assert "Runtime Doctor" in doctor_page.text_body
    assert "Doctor Checks" in doctor_page.text_body
    assert "state_event_consistency" in doctor_page.text_body
    assert "Operational Diagnostics" in doctor_page.text_body
    assert "Runtime Configuration" in doctor_page.text_body
    assert distributed_page.status_code == 200
    assert distributed_page.headers["content-type"] == "text/html; charset=utf-8"
    assert distributed_page.text_body is not None
    assert "Universal Agent Runtime Distributed" in distributed_page.text_body
    assert "Distributed Runtime" in distributed_page.text_body
    assert "not configured" in distributed_page.text_body
    assert "Distributed Health Checks" in distributed_page.text_body
    assert multi_agent_page.status_code == 200
    assert multi_agent_page.headers["content-type"] == "text/html; charset=utf-8"
    assert multi_agent_page.text_body is not None
    assert "Universal Agent Runtime Multi-Agent" in multi_agent_page.text_body
    assert "Multi-Agent registry is not configured" in multi_agent_page.text_body
    for name, title in catalog_pages.items():
        response = catalog_responses[name]
        assert response.status_code == 200
        assert response.text_body is not None
        assert f"Universal Agent Runtime {title}" in response.text_body
        assert "kubernetes@0.2.0" in response.text_body
    assert catalog_method.status_code == 405
    assert settings_page.status_code == 200
    assert settings_page.text_body is not None
    assert "Universal Agent Runtime Settings" in settings_page.text_body
    assert "Runtime Configuration" in settings_page.text_body
    assert "Configured Domains" in settings_page.text_body
    assert "kubernetes" in settings_page.text_body
    assert missing_domain_page.status_code == 404
    assert missing_package_page.status_code == 404
    assert package_method.status_code == 405
    assert unknown_detail_page.status_code == 404
    assert missing.status_code == 404
    assert missing_detail.status_code == 404
    assert invalid_limit.status_code == 400
    assert invalid_detail_limit.status_code == 400
    assert invalid_world_filter.status_code == 400
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_agentd_evaluation_console_route_renders_persisted_reports(tmp_path: Path) -> None:
    report_dir = tmp_path / "reports"
    FileEvaluationReportStore(report_dir).save(evaluation_report("nightly behavior suite"))
    service, _ = build_service([])
    app = AgentdApp(service, evaluation_report_dir=report_dir)

    evaluation_page = await app.handle(HttpRequest("GET", "/console/evaluations"))
    missing_config = await AgentdApp(service).handle(HttpRequest("GET", "/console/evaluations"))
    method_not_allowed_response = await app.handle(HttpRequest("POST", "/console/evaluations"))

    assert evaluation_page.status_code == 200
    assert evaluation_page.headers["content-type"] == "text/html; charset=utf-8"
    assert evaluation_page.text_body is not None
    assert "Universal Agent Evaluation Console" in evaluation_page.text_body
    assert "Evaluation Console" in evaluation_page.text_body
    assert "nightly behavior suite" in evaluation_page.text_body
    assert "Scenario Results" in evaluation_page.text_body
    assert "inspect_workload" in evaluation_page.text_body
    assert missing_config.status_code == 200
    assert missing_config.text_body is not None
    assert "report_dir=not configured" in missing_config.text_body
    assert "No evaluation reports" in missing_config.text_body
    assert method_not_allowed_response.status_code == 405


@pytest.mark.asyncio
async def test_agentd_session_list_route_supports_cursor_and_limit() -> None:
    service, _ = build_service(
        [
            inspect_workload(),
            finish(),
            inspect_workload(),
            finish(),
            inspect_workload(),
            finish(),
        ]
    )
    app = AgentdApp(service)

    for index in range(3):
        await app.handle(
            HttpRequest(
                "POST",
                "/v1/sessions",
                goal_submission_body(goal_description=f"Verify workload health {index}"),
            )
        )

    first_page = await app.handle(HttpRequest("GET", "/v1/sessions?limit=2"))
    first_items = first_page.body["sessions"]
    assert isinstance(first_items, list)
    first_cursor = first_page.body["next_cursor"]
    assert isinstance(first_cursor, str)
    second_page = await app.handle(HttpRequest("GET", f"/v1/sessions?after={first_cursor}&limit=2"))
    missing_cursor = await app.handle(HttpRequest("GET", "/v1/sessions?after=missing-session"))

    assert first_page.status_code == 200
    assert len(first_items) == 2
    last_first_item = first_items[-1]
    assert isinstance(last_first_item, dict)
    assert first_cursor == last_first_item["session_id"]
    second_items = second_page.body["sessions"]
    assert isinstance(second_items, list)
    assert len(second_items) == 1
    last_second_item = second_items[-1]
    assert isinstance(last_second_item, dict)
    assert second_page.body["next_cursor"] == last_second_item["session_id"]
    assert missing_cursor.status_code == 400
    error = missing_cursor.body["error"]
    assert isinstance(error, dict)
    assert error["message"] == "session cursor not found: missing-session"


@pytest.mark.asyncio
async def test_agentd_distributed_routes_expose_snapshot_and_health() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()
    coordinator.scheduler.schedule_session(SessionId("session-1"), available_at=now)
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
    coordinator.locks.acquire(
        lock_key="session/session-1",
        owner_id=DistributedLockOwnerId("worker-a"),
        ttl_seconds=999_999_999,
        now=now,
    )
    service, _ = build_service([], distributed_coordinator=coordinator)
    app = AgentdApp(service)

    snapshot = await app.handle(HttpRequest("GET", "/v1/distributed/snapshot"))
    health = await app.handle(HttpRequest("GET", "/v1/distributed/health"))
    distributed_page = await app.handle(HttpRequest("GET", "/console/distributed"))
    pruned = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/prune-terminal",
            immutable_json({"before": now.isoformat()}),
        )
    )
    prune_get = await app.handle(HttpRequest("GET", "/v1/distributed/prune-terminal"))
    invalid_prune_before = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/prune-terminal",
            immutable_json({"before": "2026-01-01T00:00:00"}),
        )
    )
    expired_work = coordinator.queue.lease(
        worker_id=WorkerId("worker-a"),
        ttl_seconds=1,
        now=now,
    )
    expired = await app.handle(HttpRequest("POST", "/v1/distributed/expire"))
    expire_get = await app.handle(HttpRequest("GET", "/v1/distributed/expire"))
    missing_service, _ = build_service([])
    missing = await AgentdApp(missing_service).handle(HttpRequest("GET", "/v1/distributed/health"))
    gap_coordinator = DistributedRuntimeCoordinator()
    gap_coordinator.scheduler.schedule_session(
        SessionId("session-without-worker"),
        available_at=now,
    )
    gap_service, _ = build_service([], distributed_coordinator=gap_coordinator)
    gap_health = await AgentdApp(gap_service).handle(HttpRequest("GET", "/v1/distributed/health"))

    assert snapshot.status_code == 200
    work_queue = snapshot.body["work_queue"]
    workers = snapshot.body["workers"]
    locks = snapshot.body["locks"]
    assert isinstance(work_queue, dict)
    assert isinstance(workers, dict)
    assert isinstance(locks, list)
    first_lock = locks[0]
    assert isinstance(first_lock, dict)
    assert work_queue["queued_count"] == 1
    assert workers["online_count"] == 1
    assert first_lock["lock_key"] == "session/session-1"
    assert health.status_code == 200
    assert distributed_page.status_code == 200
    assert distributed_page.text_body is not None
    assert "Universal Agent Runtime Distributed" in distributed_page.text_body
    assert "Distributed: ok" in distributed_page.text_body
    assert "session-1" in distributed_page.text_body
    assert "worker-a" in distributed_page.text_body
    assert "session/session-1" in distributed_page.text_body
    assert "Distributed Work Queue" in distributed_page.text_body
    assert pruned.status_code == 200
    assert pruned.body["before"] == now.isoformat()
    assert pruned.body["pruned_count"] == 1
    pruned_items = pruned.body["pruned_work_items"]
    assert isinstance(pruned_items, list)
    first_pruned = pruned_items[0]
    assert isinstance(first_pruned, dict)
    assert first_pruned["work_item_id"] == str(completed.work_item_id)
    pruned_snapshot = json_object(pruned.body["snapshot"])
    pruned_work_queue = json_object(pruned_snapshot["work_queue"])
    assert pruned_work_queue["total_count"] == 1
    assert prune_get.status_code == 405
    assert invalid_prune_before.status_code == 400
    assert invalid_prune_before.body["error"] == {
        "code": "bad_request",
        "message": "distributed prune before must include a timezone",
    }
    assert expired.status_code == 200
    expired_items = expired.body["expired_work_items"]
    assert isinstance(expired_items, list)
    first_expired = expired_items[0]
    assert isinstance(first_expired, dict)
    assert first_expired["work_item_id"] == str(expired_work.work_item_id)
    assert first_expired["status"] == "queued"
    assert expire_get.status_code == 405
    checks = health.body["checks"]
    assert isinstance(checks, list)
    first_check = checks[0]
    assert isinstance(first_check, dict)
    assert health.body["status"] == "ok"
    assert health.body["recommendations"] == []
    assert first_check["name"] == "worker_pool"
    recommendations = json_array(gap_health.body["recommendations"])
    assert {
        (
            json_string(recommendation["code"]),
            json_string(recommendation["severity"]),
            recommendation["target"],
        )
        for recommendation in (json_object(item) for item in recommendations)
    } == {
        ("start_worker_pool", "error", None),
        ("start_capable_worker", "error", "agent_session"),
    }
    assert missing.status_code == 404
    error = missing.body["error"]
    assert isinstance(error, dict)
    assert error["message"] == "distributed runtime coordinator is not configured"


@pytest.mark.asyncio
async def test_agentd_distributed_lock_lifecycle_routes() -> None:
    service, _ = build_service([], distributed_coordinator=DistributedRuntimeCoordinator())
    app = AgentdApp(service)

    acquired = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/locks/acquire",
            immutable_json(
                {
                    "lock_key": "session/session-1",
                    "owner_id": "worker-a",
                    "ttl_seconds": 30,
                    "metadata": {"reason": "run session"},
                }
            ),
        )
    )
    acquired_lock = json_object(acquired.body["lock"])
    lease_id = json_string(acquired_lock["lease_id"])
    conflict = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/locks/acquire",
            immutable_json(
                {
                    "lock_key": "session/session-1",
                    "owner_id": "worker-b",
                }
            ),
        )
    )
    heartbeat = await app.handle(
        HttpRequest(
            "POST",
            f"/v1/distributed/lock-leases/{lease_id}/heartbeat",
            immutable_json({"owner_id": "worker-a", "ttl_seconds": 60}),
        )
    )
    released = await app.handle(
        HttpRequest(
            "POST",
            f"/v1/distributed/lock-leases/{lease_id}/release",
            immutable_json({"owner_id": "worker-a"}),
        )
    )
    missing_lease = await app.handle(
        HttpRequest(
            "POST",
            f"/v1/distributed/lock-leases/{lease_id}/heartbeat",
            immutable_json({"owner_id": "worker-a"}),
        )
    )
    invalid_ttl = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/locks/acquire",
            immutable_json(
                {
                    "lock_key": "session/session-2",
                    "owner_id": "worker-a",
                    "ttl_seconds": 0,
                }
            ),
        )
    )
    invalid_metadata = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/locks/acquire",
            immutable_json(
                {
                    "lock_key": "session/session-3",
                    "owner_id": "worker-a",
                    "metadata": ["not-object"],
                }
            ),
        )
    )
    missing_service, _ = build_service([])
    missing_coordinator = await AgentdApp(missing_service).handle(
        HttpRequest(
            "POST",
            "/v1/distributed/locks/acquire",
            immutable_json({"lock_key": "session/session-1", "owner_id": "worker-a"}),
        )
    )

    assert acquired.status_code == 200
    assert acquired_lock["lock_key"] == "session/session-1"
    assert acquired_lock["metadata"] == {"reason": "run session"}
    acquired_snapshot = json_object(acquired.body["snapshot"])
    acquired_locks = json_array(acquired_snapshot["locks"])
    acquired_first_lock = json_object(acquired_locks[0])
    assert acquired_first_lock["lock_key"] == "session/session-1"
    assert conflict.status_code == 409
    conflict_error = json_object(conflict.body["error"])
    assert conflict_error["code"] == "conflict"
    assert heartbeat.status_code == 200
    heartbeat_lock = json_object(heartbeat.body["lock"])
    assert heartbeat_lock["lease_id"] == lease_id
    assert released.status_code == 200
    released_snapshot = json_object(released.body["snapshot"])
    assert released_snapshot["locks"] == []
    assert missing_lease.status_code == 404
    assert missing_lease.body["error"] == {
        "code": "not_found",
        "message": f"lock lease not found: {lease_id}",
    }
    assert invalid_ttl.status_code == 400
    assert invalid_ttl.body["error"] == {
        "code": "bad_request",
        "message": "distributed lock ttl_seconds must be a positive number",
    }
    assert invalid_metadata.status_code == 400
    assert invalid_metadata.body["error"] == {
        "code": "bad_request",
        "message": "distributed lock metadata must be an object",
    }
    assert missing_coordinator.status_code == 404
    assert missing_coordinator.body["error"] == {
        "code": "not_found",
        "message": "distributed runtime coordinator is not configured",
    }


@pytest.mark.asyncio
async def test_agentd_distributed_worker_lifecycle_routes() -> None:
    service, _ = build_service([], distributed_coordinator=DistributedRuntimeCoordinator())
    app = AgentdApp(service)

    registered = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/workers/worker-a/register",
            immutable_json(
                {
                    "capabilities": ["agent_session"],
                    "metadata": {"host": "local"},
                    "ttl_seconds": 30,
                }
            ),
        )
    )
    heartbeat = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/workers/worker-a/heartbeat",
            immutable_json({"ttl_seconds": 60}),
        )
    )
    draining = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/workers/worker-a/drain",
            immutable_json({"reason": "finish current lease"}),
        )
    )
    offline = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/workers/worker-a/offline",
            immutable_json({"reason": "shutdown complete"}),
        )
    )
    wrong_method = await app.handle(HttpRequest("GET", "/v1/distributed/workers/worker-a/register"))
    invalid_capability = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/workers/worker-b/register",
            immutable_json({"capabilities": [1]}),
        )
    )
    invalid_ttl = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/workers/worker-a/heartbeat",
            immutable_json({"ttl_seconds": 0}),
        )
    )
    invalid_metadata = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/workers/worker-c/register",
            immutable_json({"metadata": ["not-object"]}),
        )
    )
    missing_worker = await app.handle(
        HttpRequest("POST", "/v1/distributed/workers/worker-missing/heartbeat")
    )
    missing_service, _ = build_service([])
    missing_coordinator = await AgentdApp(missing_service).handle(
        HttpRequest("POST", "/v1/distributed/workers/worker-a/register")
    )

    assert registered.status_code == 200
    worker = registered.body["worker"]
    assert isinstance(worker, dict)
    assert worker["worker_id"] == "worker-a"
    assert worker["status"] == "online"
    assert worker["capabilities"] == ["agent_session"]
    assert worker["metadata"] == {"host": "local"}
    registered_snapshot = json_object(registered.body["snapshot"])
    registered_workers = json_object(registered_snapshot["workers"])
    assert registered_workers["online_count"] == 1
    heartbeat_worker = json_object(heartbeat.body["worker"])
    assert heartbeat_worker["status"] == "online"
    draining_worker = json_object(draining.body["worker"])
    assert draining_worker["status"] == "draining"
    assert draining_worker["last_error"] == "finish current lease"
    offline_worker = json_object(offline.body["worker"])
    assert offline_worker["status"] == "offline"
    offline_snapshot = json_object(offline.body["snapshot"])
    offline_workers = json_object(offline_snapshot["workers"])
    assert offline_workers["offline_count"] == 1
    assert wrong_method.status_code == 405
    assert invalid_capability.status_code == 400
    assert invalid_capability.body["error"] == {
        "code": "bad_request",
        "message": "distributed worker capabilities[0] must be a non-empty string",
    }
    assert invalid_ttl.status_code == 400
    assert invalid_ttl.body["error"] == {
        "code": "bad_request",
        "message": "distributed worker ttl_seconds must be a positive number",
    }
    assert invalid_metadata.status_code == 400
    assert invalid_metadata.body["error"] == {
        "code": "bad_request",
        "message": "distributed worker metadata must be an object",
    }
    assert missing_worker.status_code == 404
    assert missing_worker.body["error"] == {
        "code": "not_found",
        "message": "worker not found: worker-missing",
    }
    assert missing_coordinator.status_code == 404
    assert missing_coordinator.body["error"] == {
        "code": "not_found",
        "message": "distributed runtime coordinator is not configured",
    }


@pytest.mark.asyncio
async def test_agentd_distributed_worker_run_once_route() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, _ = build_service(
        [wait(), inspect_workload(), finish()],
        distributed_coordinator=coordinator,
    )
    waiting = await service.run_goal(*goal_task())
    service.distributed_schedule_session(waiting.result.session_id)
    app = AgentdApp(service)

    response = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/workers/worker-a/run-once",
            immutable_json(
                {
                    "lease_ttl_seconds": 30,
                    "worker_ttl_seconds": 30,
                    "heartbeat_interval_seconds": 10,
                }
            ),
        )
    )
    completed = await service.get_session(waiting.result.session_id)

    assert response.status_code == 200
    assert response.body["status"] == "completed"
    assert response.body["worker_id"] == "worker-a"
    work_item = json_object(response.body["work_item"])
    assert work_item["status"] == "completed"
    assert completed.goal_status.value == "completed"


@pytest.mark.asyncio
async def test_agentd_distributed_worker_run_route_resumes_backlog() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, _ = build_service(
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
    app = AgentdApp(service)

    response = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/workers/worker-a/run",
            immutable_json(
                {
                    "max_items": 5,
                    "lease_ttl_seconds": 30,
                    "worker_ttl_seconds": 30,
                    "heartbeat_interval_seconds": 10,
                }
            ),
        )
    )

    assert response.status_code == 200
    results = json_array(response.body["results"])
    assert [json_object(item)["status"] for item in results] == [
        "completed",
        "completed",
        "no_work",
    ]
    assert response.body["processed_count"] == 2
    assert response.body["terminal_status"] == "no_work"
    assert (await service.get_session(first.result.session_id)).goal_status.value == "completed"
    assert (await service.get_session(second.result.session_id)).goal_status.value == "completed"


@pytest.mark.asyncio
async def test_agentd_distributed_schedule_route_schedules_session_work() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()
    coordinator.workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=999_999_999,
        now=now,
    )
    service, _ = build_service([], distributed_coordinator=coordinator)
    app = AgentdApp(service)

    scheduled = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/sessions/session-1/schedule",
            immutable_json(
                {
                    "payload": {"goal": "verify workload health"},
                    "priority": 4,
                    "max_attempts": 2,
                }
            ),
        )
    )
    wrong_method = await app.handle(
        HttpRequest("GET", "/v1/distributed/sessions/session-1/schedule")
    )
    invalid_priority = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/sessions/session-1/schedule",
            immutable_json({"priority": "high"}),
        )
    )
    missing_service, _ = build_service([])
    missing_coordinator = await AgentdApp(missing_service).handle(
        HttpRequest("POST", "/v1/distributed/sessions/session-1/schedule")
    )

    assert scheduled.status_code == 200
    scheduled_item = scheduled.body["scheduled_work_item"]
    assert isinstance(scheduled_item, dict)
    assert scheduled_item["kind"] == "agent_session"
    assert scheduled_item["status"] == "queued"
    snapshot = scheduled.body["snapshot"]
    assert isinstance(snapshot, dict)
    work_queue = snapshot["work_queue"]
    assert isinstance(work_queue, dict)
    assert work_queue["queued_count"] == 1
    health = json_object(scheduled.body["health"])
    assert health["status"] == "ok"
    assert wrong_method.status_code == 405
    assert invalid_priority.status_code == 400
    assert invalid_priority.body["error"] == {
        "code": "bad_request",
        "message": "distributed schedule priority must be an integer",
    }
    assert missing_coordinator.status_code == 404
    assert missing_coordinator.body["error"] == {
        "code": "not_found",
        "message": "distributed runtime coordinator is not configured",
    }


@pytest.mark.asyncio
async def test_agentd_distributed_schedule_task_route_runs_from_worker() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, _ = build_service(
        [wait(), inspect_workload(), finish()],
        distributed_coordinator=coordinator,
    )
    waiting = await service.run_goal(*goal_task())
    app = AgentdApp(service)

    scheduled = await app.handle(
        HttpRequest(
            "POST",
            (
                f"/v1/distributed/sessions/{waiting.result.session_id}/tasks/"
                f"{waiting.session.current_task_id}/schedule"
            ),
            immutable_json({"priority": 4}),
        )
    )
    worker = await app.handle(HttpRequest("POST", "/v1/distributed/workers/worker-a/run-once"))
    completed = await service.get_session(waiting.result.session_id)

    assert scheduled.status_code == 200
    scheduled_item = json_object(scheduled.body["scheduled_work_item"])
    assert scheduled_item["kind"] == "task"
    assert scheduled_item["status"] == "queued"
    assert worker.status_code == 200
    assert worker.body["status"] == "completed"
    work_item = json_object(worker.body["work_item"])
    assert work_item["status"] == "completed"
    assert completed.goal_status.value == "completed"


@pytest.mark.asyncio
async def test_agentd_distributed_schedule_action_route_confirms_pending_action() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, backend = build_remediation_service(
        [
            inspect_workload("healthy"),
            inspect_pod(),
            scale_workload(),
            inspect_workload("verification_observed", "healthy"),
            finish(),
        ],
        distributed_coordinator=coordinator,
    )
    waiting = await service.run_goal(*remediation_goal_task())
    assert waiting.session.pending_action is not None
    app = AgentdApp(service)

    scheduled = await app.handle(
        HttpRequest(
            "POST",
            (
                f"/v1/distributed/sessions/{waiting.result.session_id}/tasks/"
                f"{waiting.session.current_task_id}/actions/"
                f"{waiting.session.pending_action.action_id}/schedule"
            ),
            immutable_json({"confirmed": True, "priority": 4}),
        )
    )
    worker = await app.handle(HttpRequest("POST", "/v1/distributed/workers/worker-a/run-once"))
    completed = await service.get_session(waiting.result.session_id)

    assert scheduled.status_code == 200
    scheduled_item = json_object(scheduled.body["scheduled_work_item"])
    assert scheduled_item["kind"] == "tool_action"
    assert scheduled_item["status"] == "queued"
    assert scheduled_item["action_id"] == str(waiting.session.pending_action.action_id)
    assert worker.status_code == 200
    assert worker.body["status"] == "completed"
    work_item = json_object(worker.body["work_item"])
    assert work_item["status"] == "completed"
    assert completed.goal_status.value == "completed"
    assert completed.pending_action is None
    assert backend.mutation_calls == 1


@pytest.mark.asyncio
async def test_agentd_distributed_schedule_pending_actions_route_confirms_pending_actions() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, backend = build_remediation_service(
        [
            inspect_workload("healthy"),
            inspect_pod(),
            scale_workload(),
            inspect_workload("verification_observed", "healthy"),
            finish(),
        ],
        distributed_coordinator=coordinator,
    )
    waiting = await service.run_goal(*remediation_goal_task())
    assert waiting.session.pending_action is not None
    pending = waiting.session.pending_action
    app = AgentdApp(service)

    scheduled = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/pending-actions/schedule",
            immutable_json({"confirmed": True, "priority": 4}),
        )
    )
    worker = await app.handle(HttpRequest("POST", "/v1/distributed/workers/worker-a/run-once"))
    completed = await service.get_session(waiting.result.session_id)

    assert scheduled.status_code == 202
    assert scheduled.body["scheduled_count"] == 1
    scheduled_items = json_array(scheduled.body["scheduled_work_items"])
    scheduled_item = json_object(scheduled_items[0])
    assert scheduled_item["kind"] == "tool_action"
    assert scheduled_item["status"] == "queued"
    assert scheduled_item["action_id"] == str(pending.action_id)
    assert worker.status_code == 200
    assert worker.body["status"] == "completed"
    assert completed.goal_status.value == "completed"
    assert completed.pending_action is None
    assert backend.mutation_calls == 1


@pytest.mark.asyncio
async def test_agentd_distributed_schedule_action_route_validates_confirmation() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, _ = build_remediation_service(
        [inspect_workload("healthy"), inspect_pod(), scale_workload()],
        distributed_coordinator=coordinator,
    )
    waiting = await service.run_goal(*remediation_goal_task())
    assert waiting.session.pending_action is not None
    route = (
        f"/v1/distributed/sessions/{waiting.result.session_id}/tasks/"
        f"{waiting.session.current_task_id}/actions/"
        f"{waiting.session.pending_action.action_id}/schedule"
    )
    app = AgentdApp(service)

    missing = await app.handle(HttpRequest("POST", route))
    false_confirmation = await app.handle(
        HttpRequest("POST", route, immutable_json({"confirmed": False}))
    )
    wrong_method = await app.handle(HttpRequest("GET", route))

    assert missing.status_code == 400
    assert missing.body["error"] == {
        "code": "bad_request",
        "message": "distributed schedule-action confirmed must be a boolean",
    }
    assert false_confirmation.status_code == 400
    assert false_confirmation.body["error"] == {
        "code": "bad_request",
        "message": "distributed schedule-action requires confirmed=true",
    }
    assert wrong_method.status_code == 405
    assert wrong_method.headers["allow"] == "POST"


@pytest.mark.asyncio
async def test_agentd_distributed_schedule_pending_actions_route_validates_confirmation() -> None:
    service, _ = build_remediation_service(
        [], distributed_coordinator=DistributedRuntimeCoordinator()
    )
    app = AgentdApp(service)
    route = "/v1/distributed/pending-actions/schedule"

    missing = await app.handle(HttpRequest("POST", route))
    false_confirmation = await app.handle(
        HttpRequest("POST", route, immutable_json({"confirmed": False}))
    )
    wrong_method = await app.handle(HttpRequest("GET", route))

    assert missing.status_code == 400
    assert missing.body["error"] == {
        "code": "bad_request",
        "message": "distributed pending-action schedule confirmed must be a boolean",
    }
    assert false_confirmation.status_code == 400
    assert false_confirmation.body["error"] == {
        "code": "bad_request",
        "message": "distributed pending-action schedule requires confirmed=true",
    }
    assert wrong_method.status_code == 405
    assert wrong_method.headers["allow"] == "POST"


@pytest.mark.asyncio
async def test_agentd_distributed_schedule_goal_route_runs_from_worker() -> None:
    coordinator = DistributedRuntimeCoordinator()
    service, _ = build_service(
        [inspect_workload(), finish()],
        distributed_coordinator=coordinator,
    )
    app = AgentdApp(service)

    scheduled = await app.handle(
        HttpRequest(
            "POST",
            "/v1/distributed/goals",
            goal_submission_body(),
        )
    )
    worker = await app.handle(HttpRequest("POST", "/v1/distributed/workers/worker-a/run-once"))
    sessions = await service.list_sessions()

    assert scheduled.status_code == 202
    scheduled_item = json_object(scheduled.body["scheduled_work_item"])
    assert scheduled_item["kind"] == "agent_goal"
    assert scheduled_item["status"] == "queued"
    assert worker.status_code == 200
    assert worker.body["status"] == "completed"
    work_item = json_object(worker.body["work_item"])
    assert work_item["status"] == "completed"
    assert len(sessions) == 1
    assert sessions[0].goal_status.value == "completed"


@pytest.mark.asyncio
async def test_agentd_distributed_cancel_route_cancels_work_item() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    coordinator = DistributedRuntimeCoordinator()
    scheduled = coordinator.scheduler.schedule_session(SessionId("session-1"), available_at=now)
    coordinator.workers.register(
        WorkerId("worker-a"),
        capabilities=("agent_session",),
        ttl_seconds=999_999_999,
        now=now,
    )
    service, _ = build_service([], distributed_coordinator=coordinator)
    app = AgentdApp(service)

    cancelled = await app.handle(
        HttpRequest(
            "POST",
            f"/v1/distributed/work-items/{scheduled.work_item_id}/cancel",
            immutable_json({"reason": "operator cancelled distributed work"}),
        )
    )
    wrong_method = await app.handle(
        HttpRequest("GET", f"/v1/distributed/work-items/{scheduled.work_item_id}/cancel")
    )
    invalid_reason = await app.handle(
        HttpRequest(
            "POST",
            f"/v1/distributed/work-items/{scheduled.work_item_id}/cancel",
            immutable_json({"reason": 42}),
        )
    )
    missing_work = await app.handle(
        HttpRequest("POST", "/v1/distributed/work-items/work-missing/cancel")
    )
    missing_service, _ = build_service([])
    missing_coordinator = await AgentdApp(missing_service).handle(
        HttpRequest(
            "POST",
            f"/v1/distributed/work-items/{scheduled.work_item_id}/cancel",
        )
    )

    assert cancelled.status_code == 200
    cancelled_item = cancelled.body["cancelled_work_item"]
    assert isinstance(cancelled_item, dict)
    assert cancelled_item["work_item_id"] == str(scheduled.work_item_id)
    assert cancelled_item["status"] == "cancelled"
    assert cancelled_item["last_error"] == "operator cancelled distributed work"
    snapshot = cancelled.body["snapshot"]
    assert isinstance(snapshot, dict)
    work_queue = snapshot["work_queue"]
    assert isinstance(work_queue, dict)
    assert work_queue["queued_count"] == 0
    assert work_queue["cancelled_count"] == 1
    health = cancelled.body["health"]
    assert isinstance(health, dict)
    checks = health["checks"]
    assert isinstance(checks, list)
    assert health["status"] == "ok"
    assert {check["name"] for check in checks if isinstance(check, dict)} >= {
        "worker_pool",
        "worker_registry",
    }
    assert wrong_method.status_code == 405
    assert invalid_reason.status_code == 400
    assert invalid_reason.body["error"] == {
        "code": "bad_request",
        "message": "distributed cancel reason must be a string",
    }
    assert missing_work.status_code == 404
    assert missing_work.body["error"] == {
        "code": "not_found",
        "message": "work item not found: work-missing",
    }
    assert missing_coordinator.status_code == 404
    assert missing_coordinator.body["error"] == {
        "code": "not_found",
        "message": "distributed runtime coordinator is not configured",
    }


@pytest.mark.asyncio
async def test_agentd_operations_routes_expose_metrics_doctor_and_audit() -> None:
    service, backend = build_service(
        [scale_workload(), inspect_workload(), finish()],
        usage=[
            ModelUsage(
                "scripted",
                "agentd-test",
                input_tokens=90,
                output_tokens=20,
                estimated_cost_micros=25,
            ),
            ModelUsage(
                "scripted",
                "agentd-test",
                input_tokens=60,
                output_tokens=10,
                estimated_cost_micros=10,
            ),
            ModelUsage("scripted", "agentd-test", input_tokens=30, output_tokens=5),
        ],
    )
    app = AgentdApp(service)

    created = await app.handle(HttpRequest("POST", "/v1/sessions", goal_submission_body()))
    result = created.body["result"]
    assert isinstance(result, dict)
    session_id = result["session_id"]
    assert isinstance(session_id, str)

    metrics = await app.handle(HttpRequest("GET", "/v1/metrics"))
    prometheus_metrics = await app.handle(HttpRequest("GET", "/v1/metrics/prometheus"))
    cost = await app.handle(HttpRequest("GET", "/v1/cost"))
    doctor = await app.handle(HttpRequest("GET", "/v1/doctor"))
    audit = await app.handle(HttpRequest("GET", "/v1/audit"))
    session_audit = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/audit"))
    session_cost = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/cost"))
    logs = await app.handle(HttpRequest("GET", "/v1/logs"))
    session_logs = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/logs"))
    traces = await app.handle(HttpRequest("GET", "/v1/traces"))
    session_traces = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/traces"))
    otlp_traces = await app.handle(HttpRequest("GET", "/v1/traces/otlp"))
    session_otlp_traces = await app.handle(
        HttpRequest("GET", f"/v1/sessions/{session_id}/traces/otlp")
    )

    assert metrics.status_code == 200
    assert metrics.body["session_count"] == 1
    assert metrics.body["completed_goal_count"] == 1
    assert metrics.body["action_started_count"] == 2
    assert metrics.body["decision_generated_count"] == 3
    assert metrics.body["decision_validated_count"] == 3
    assert metrics.body["decision_rejected_count"] == 0
    assert metrics.body["model_call_count"] == 3
    assert metrics.body["model_total_token_count"] == 215
    assert prometheus_metrics.status_code == 200
    assert prometheus_metrics.headers["content-type"] == (
        "text/plain; version=0.0.4; charset=utf-8"
    )
    assert prometheus_metrics.text_body is not None
    assert "universal_agent_runtime_completed_goals 1.0\n" in prometheus_metrics.text_body
    assert "universal_agent_runtime_decisions_validated 3.0\n" in prometheus_metrics.text_body
    assert "universal_agent_runtime_model_total_tokens 215.0\n" in prometheus_metrics.text_body
    assert cost.status_code == 200
    assert cost.body == session_cost.body
    assert cost.body["model_call_count"] == 3
    assert cost.body["total_tokens"] == 215
    assert cost.body["estimated_cost_micros"] == 35
    by_model = cost.body["by_model"]
    assert isinstance(by_model, list)
    first_cost_item = by_model[0]
    assert isinstance(first_cost_item, dict)
    assert first_cost_item["model"] == "agentd-test"
    assert doctor.status_code == 200
    assert doctor.body["status"] == "ok"
    assert logs.status_code == 200
    assert logs.body == session_logs.body
    log_items = logs.body["logs"]
    assert isinstance(log_items, list)
    last_log_item = log_items[-1]
    assert isinstance(last_log_item, dict)
    assert last_log_item["event_type"] == "GoalCompleted"
    assert traces.status_code == 200
    assert traces.body == session_traces.body
    span_items = traces.body["spans"]
    assert isinstance(span_items, list)
    root_span = span_items[0]
    assert isinstance(root_span, dict)
    assert root_span["name"] == "runtime.session"
    assert root_span["status"] == "ok"
    action_span_items = [
        item
        for item in span_items[1:]
        if isinstance(item, dict) and str(item["name"]).startswith("runtime.action.")
    ]
    action_span_names = [item["name"] for item in action_span_items]
    assert action_span_names == [
        "runtime.action.scale_workload",
        "runtime.action.inspect_workload",
    ]
    phase_span_names = {
        item["name"]
        for item in span_items[1:]
        if isinstance(item, dict) and not str(item["name"]).startswith("runtime.action.")
    }
    assert phase_span_names >= {
        "runtime.decision",
        "runtime.model_usage",
        "runtime.policy",
        "runtime.observation",
        "runtime.evaluation",
    }
    assert all(item["parent_span_id"] == root_span["span_id"] for item in action_span_items)
    assert otlp_traces.status_code == 200
    assert otlp_traces.body == session_otlp_traces.body
    resource_spans = otlp_traces.body["resourceSpans"]
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
    exported_root = exported_spans[0]
    exported_action = exported_spans[1]
    assert isinstance(exported_root, dict)
    assert isinstance(exported_action, dict)
    assert exported_action["kind"] == "SPAN_KIND_CLIENT"
    assert exported_action["parentSpanId"] == exported_root["spanId"]
    assert audit.status_code == 200
    audit_items = audit.body["audit_records"]
    session_audit_items = session_audit.body["audit_records"]
    assert isinstance(audit_items, list)
    assert isinstance(session_audit_items, list)
    assert audit_items == session_audit_items
    assert len(audit_items) == 1
    record = audit_items[0]
    assert isinstance(record, dict)
    assert record["session_id"] == session_id
    assert record["capability"] == "scale_workload"
    assert record["policy_effect"] == "allow"
    assert record["status"] == "succeeded"
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_agentd_events_route_supports_cursor_and_limit() -> None:
    service, _ = build_service([inspect_workload(), finish()])
    app = AgentdApp(service)
    created = await app.handle(HttpRequest("POST", "/v1/sessions", goal_submission_body()))
    result = created.body["result"]
    assert isinstance(result, dict)
    session_id = result["session_id"]
    assert isinstance(session_id, str)

    all_events = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/events"))
    event_items = all_events.body["events"]
    assert isinstance(event_items, list)
    first_event = event_items[0]
    assert isinstance(first_event, dict)

    streamed = await app.handle(
        HttpRequest(
            "GET", f"/v1/sessions/{session_id}/events?after={first_event['event_id']}&limit=2"
        )
    )

    assert streamed.status_code == 200
    streamed_items = streamed.body["events"]
    assert isinstance(streamed_items, list)
    assert len(streamed_items) == 2
    assert streamed_items == event_items[1:3]
    last_streamed = streamed_items[-1]
    assert isinstance(last_streamed, dict)
    assert streamed.body["next_cursor"] == last_streamed["event_id"]


@pytest.mark.asyncio
async def test_agentd_events_stream_route_projects_cursor_batch_as_sse() -> None:
    service, _ = build_service([inspect_workload(), finish()])
    app = AgentdApp(service)
    created = await app.handle(HttpRequest("POST", "/v1/sessions", goal_submission_body()))
    result = created.body["result"]
    assert isinstance(result, dict)
    session_id = result["session_id"]
    assert isinstance(session_id, str)

    response = await app.handle(
        HttpRequest("GET", f"/v1/sessions/{session_id}/events/stream?limit=2")
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.text_body is not None
    assert response.text_body.count("\n\n") == 3
    assert "event: GoalCreated\n" in response.text_body
    assert "data: " in response.text_body
    assert f": next_cursor={response.body['next_cursor']}\n\n" in response.text_body
    events_response = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/events"))
    event_items = events_response.body["events"]
    assert isinstance(event_items, list)
    last_event = event_items[-1]
    assert isinstance(last_event, dict)
    last_cursor = last_event["event_id"]
    assert isinstance(last_cursor, str)
    heartbeat = await app.handle(
        HttpRequest(
            "GET",
            (
                f"/v1/sessions/{session_id}/events/stream?after={last_cursor}"
                "&wait=true&timeout_seconds=0"
            ),
        )
    )

    assert heartbeat.status_code == 200
    assert heartbeat.body["events"] == []
    assert heartbeat.body["next_cursor"] == last_cursor
    assert heartbeat.text_body == f": heartbeat\n\n: next_cursor={last_cursor}\n\n"

    invalid_wait = await app.handle(
        HttpRequest("GET", f"/v1/sessions/{session_id}/events/stream?wait=maybe")
    )
    invalid_timeout = await app.handle(
        HttpRequest(
            "GET",
            f"/v1/sessions/{session_id}/events/stream?wait=true&timeout_seconds=31",
        )
    )

    assert invalid_wait.status_code == 400
    assert invalid_wait.body["error"] == {
        "code": "bad_request",
        "message": "wait must be a boolean",
    }
    assert invalid_timeout.status_code == 400
    assert invalid_timeout.body["error"] == {
        "code": "bad_request",
        "message": "timeout_seconds must be between 0 and 30",
    }


@pytest.mark.asyncio
async def test_agentd_events_stream_waits_for_new_session_events() -> None:
    service, _ = build_service([wait(), inspect_workload(), finish()])
    app = AgentdApp(service)
    created = await app.handle(HttpRequest("POST", "/v1/sessions", goal_submission_body()))
    result = created.body["result"]
    assert isinstance(result, dict)
    session_id = result["session_id"]
    assert isinstance(session_id, str)
    events_response = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/events"))
    event_items = events_response.body["events"]
    assert isinstance(event_items, list)
    last_event = event_items[-1]
    assert isinstance(last_event, dict)
    last_cursor = last_event["event_id"]
    assert isinstance(last_cursor, str)

    wait_task = asyncio.create_task(
        app.handle(
            HttpRequest(
                "GET",
                (
                    f"/v1/sessions/{session_id}/events/stream?after={last_cursor}"
                    "&wait=true&timeout_seconds=1&poll_interval_seconds=0.001"
                ),
            )
        )
    )
    await asyncio.sleep(0.01)
    resumed = await app.handle(HttpRequest("POST", f"/v1/sessions/{session_id}/resume"))
    response = await wait_task

    assert resumed.status_code == 200
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream"
    assert response.text_body is not None
    assert (
        "event: StateUpdated\n" in response.text_body
        or "event: GoalCompleted\n" in response.text_body
    )
    assert response.body["events"]


@pytest.mark.asyncio
async def test_agentd_pause_and_resume_routes_continue_waiting_session_without_pending_action() -> (
    None
):
    service, backend = build_service([wait(), inspect_workload(), finish()])
    app = AgentdApp(service)
    created = await app.handle(HttpRequest("POST", "/v1/sessions", goal_submission_body()))
    result = created.body["result"]
    assert isinstance(result, dict)
    session_id = result["session_id"]
    assert isinstance(session_id, str)

    paused = await app.handle(
        HttpRequest(
            "POST",
            f"/v1/sessions/{session_id}/pause",
            immutable_json({"reason": "operator paused via route"}),
        )
    )
    resumed = await app.handle(HttpRequest("POST", f"/v1/sessions/{session_id}/resume"))
    events = await app.handle(HttpRequest("GET", f"/v1/sessions/{session_id}/events"))

    paused_result = paused.body["result"]
    resumed_result = resumed.body["result"]
    assert isinstance(paused_result, dict)
    assert isinstance(resumed_result, dict)
    assert paused.status_code == 200
    assert paused_result["status"] == "waiting"
    assert resumed.status_code == 200
    assert resumed_result["status"] == "completed"
    assert backend.inspect_calls == 1
    event_items = events.body["events"]
    assert isinstance(event_items, list)
    event_types = [item["type"] for item in event_items if isinstance(item, dict)]
    assert "SessionPaused" in event_types
    assert "SessionResumed" in event_types


@pytest.mark.asyncio
async def test_agentd_session_and_events_routes_are_json_safe() -> None:
    service, backend = build_service([inspect_workload(), finish()])
    app = AgentdApp(service)
    run = await service.run_goal(*goal_task())

    session = await app.handle(HttpRequest("GET", f"/v1/sessions/{run.result.session_id}"))
    events = await app.handle(HttpRequest("GET", f"/v1/sessions/{run.result.session_id}/events"))

    assert run.result.status is ExecutionStatus.COMPLETED
    assert session.status_code == 200
    assert session.body["session_id"] == str(run.result.session_id)
    assert session.body["goal_status"] == "completed"
    assert session.body["current_task_status"] == "completed"
    assert session.body["latest_evaluation"] == {
        "status": "completed",
        "reason": "workload health criteria satisfied",
        "evaluator_name": "workload-health",
        "matched_criteria": {"healthy": True},
        "task_completed": True,
        "goal_completed": True,
    }
    assert events.status_code == 200
    event_items = events.body["events"]
    assert isinstance(event_items, list)
    last_event = event_items[-1]
    assert isinstance(last_event, dict)
    assert last_event["type"] == "GoalCompleted"
    assert isinstance(last_event["occurred_at"], str)
    assert events.body["next_cursor"] == last_event["event_id"]
    assert backend.inspect_calls == 1


@pytest.mark.asyncio
async def test_agentd_resume_route_confirms_pending_action() -> None:
    service, backend = build_remediation_service(
        [
            inspect_workload("healthy"),
            inspect_pod(),
            scale_workload(),
            inspect_workload("verification_observed", "healthy"),
            finish(),
        ]
    )
    app = AgentdApp(service)
    waiting = await service.run_goal(*remediation_goal_task())

    response = await app.handle(
        HttpRequest(
            "POST",
            f"/v1/sessions/{waiting.result.session_id}/resume",
            immutable_json({"confirmed": True}),
        )
    )

    assert response.status_code == 200
    result = response.body["result"]
    session = response.body["session"]
    assert isinstance(result, dict)
    assert isinstance(session, dict)
    assert result["status"] == "completed"
    assert result["error_code"] is None
    assert session["goal_status"] == "completed"
    assert session["pending_action"] is None
    assert backend.mutation_calls == 1


@pytest.mark.asyncio
async def test_agentd_resume_route_rejects_pending_action() -> None:
    service, backend = build_remediation_service(
        [inspect_workload("healthy"), inspect_pod(), scale_workload()]
    )
    app = AgentdApp(service)
    waiting = await service.run_goal(*remediation_goal_task())

    response = await app.handle(
        HttpRequest(
            "POST",
            f"/v1/sessions/{waiting.result.session_id}/resume",
            immutable_json({"confirmed": False}),
        )
    )

    assert response.status_code == 200
    result = response.body["result"]
    session = response.body["session"]
    assert isinstance(result, dict)
    assert isinstance(session, dict)
    assert result["status"] == "failed"
    assert result["error_code"] == "confirmation_rejected"
    assert session["goal_status"] == "failed"
    assert session["pending_action"] is None
    assert backend.mutation_calls == 0


@pytest.mark.asyncio
async def test_agentd_cancel_route_cancels_pending_action() -> None:
    service, backend = build_remediation_service(
        [inspect_workload("healthy"), inspect_pod(), scale_workload()]
    )
    app = AgentdApp(service)
    waiting = await service.run_goal(*remediation_goal_task())

    response = await app.handle(
        HttpRequest(
            "POST",
            f"/v1/sessions/{waiting.result.session_id}/cancel",
            immutable_json({"reason": "operator cancelled the session"}),
        )
    )

    assert response.status_code == 200
    result = response.body["result"]
    session = response.body["session"]
    assert isinstance(result, dict)
    assert isinstance(session, dict)
    assert result["status"] == "cancelled"
    assert result["error_code"] is None
    assert result["reason"] == "operator cancelled the session"
    assert session["goal_status"] == "cancelled"
    assert session["current_task_status"] == "cancelled"
    assert session["pending_action"] is None
    assert backend.mutation_calls == 0


@pytest.mark.asyncio
async def test_agentd_create_session_route_validates_request_body() -> None:
    service, _ = build_service([])
    app = AgentdApp(service)
    invalid_goal: dict[str, JsonValue] = {
        "description": "",
        "success_criteria": [{"key": "healthy", "expected": True}],
    }
    valid_task: dict[str, JsonValue] = {
        "description": "Inspect workload",
        "required_criteria": ["healthy"],
    }
    empty_criteria_goal: dict[str, JsonValue] = {
        "description": "Verify workload health",
        "success_criteria": [],
    }
    invalid_required_task: dict[str, JsonValue] = {
        "description": "Inspect workload",
        "required_criteria": [1],
    }
    missing_task_description: dict[str, JsonValue] = {
        "required_criteria": ["healthy"],
    }
    valid_goal: dict[str, JsonValue] = {
        "description": "Verify workload health",
        "success_criteria": [{"key": "healthy", "expected": True}],
    }

    wrong_method = await app.handle(HttpRequest("PUT", "/v1/sessions"))
    missing_goal = await app.handle(HttpRequest("POST", "/v1/sessions"))
    empty_goal_description = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions",
            immutable_json({"goal": invalid_goal, "task": valid_task}),
        )
    )
    empty_success_criteria = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions",
            immutable_json({"goal": empty_criteria_goal, "task": valid_task}),
        )
    )
    invalid_required_criteria = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions",
            immutable_json({"goal": valid_goal, "task": invalid_required_task}),
        )
    )
    invalid_profile = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions",
            immutable_json({"profile": 1, "goal": valid_goal, "task": valid_task}),
        )
    )
    invalid_goal_object = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions",
            immutable_json({"goal": [], "task": valid_task}),
        )
    )
    missing_task_field = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions",
            immutable_json({"goal": valid_goal, "task": missing_task_description}),
        )
    )

    assert wrong_method.status_code == 405
    assert wrong_method.headers["allow"] == "GET, POST"
    assert missing_goal.status_code == 400
    assert missing_goal.body["error"] == {
        "code": "bad_request",
        "message": "goal is required",
    }
    assert empty_goal_description.status_code == 400
    assert empty_goal_description.body["error"] == {
        "code": "bad_request",
        "message": "goal.description must not be empty",
    }
    assert empty_success_criteria.status_code == 400
    assert empty_success_criteria.body["error"] == {
        "code": "bad_request",
        "message": "goal.success_criteria must not be empty",
    }
    assert invalid_required_criteria.status_code == 400
    assert invalid_required_criteria.body["error"] == {
        "code": "bad_request",
        "message": "task.required_criteria[0] must be a string",
    }
    assert invalid_profile.status_code == 400
    assert invalid_profile.body["error"] == {
        "code": "bad_request",
        "message": "profile must be a string",
    }
    assert invalid_goal_object.status_code == 400
    assert invalid_goal_object.body["error"] == {
        "code": "bad_request",
        "message": "goal must be an object",
    }
    assert missing_task_field.status_code == 400
    assert missing_task_field.body["error"] == {
        "code": "bad_request",
        "message": "task.description is required",
    }


@pytest.mark.asyncio
async def test_agentd_resume_route_validates_request_body() -> None:
    service, _ = build_service([])
    app = AgentdApp(service)

    wrong_method = await app.handle(HttpRequest("GET", "/v1/sessions/session-1/resume"))
    pause_wrong_method = await app.handle(HttpRequest("GET", "/v1/sessions/session-1/pause"))
    cancel_wrong_method = await app.handle(HttpRequest("GET", "/v1/sessions/session-1/cancel"))
    missing_session_without_confirmed = await app.handle(
        HttpRequest("POST", "/v1/sessions/session-1/resume")
    )
    invalid_confirmed = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions/session-1/resume",
            immutable_json({"confirmed": "true"}),
        )
    )
    invalid_pause_reason = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions/session-1/pause",
            immutable_json({"reason": 42}),
        )
    )
    invalid_cancel_reason = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions/session-1/cancel",
            immutable_json({"reason": 42}),
        )
    )

    assert wrong_method.status_code == 405
    assert wrong_method.headers["allow"] == "POST"
    assert pause_wrong_method.status_code == 405
    assert pause_wrong_method.headers["allow"] == "POST"
    assert cancel_wrong_method.status_code == 405
    assert cancel_wrong_method.headers["allow"] == "POST"
    assert missing_session_without_confirmed.status_code == 404
    assert missing_session_without_confirmed.body["error"] == {
        "code": "not_found",
        "message": "session not found: session-1",
    }
    assert invalid_confirmed.status_code == 400
    assert invalid_confirmed.body["error"] == {
        "code": "bad_request",
        "message": "resume confirmed must be a boolean",
    }
    assert invalid_pause_reason.status_code == 400
    assert invalid_pause_reason.body["error"] == {
        "code": "bad_request",
        "message": "pause reason must be a string",
    }
    assert invalid_cancel_reason.status_code == 400
    assert invalid_cancel_reason.body["error"] == {
        "code": "bad_request",
        "message": "cancel reason must be a string",
    }


@pytest.mark.asyncio
async def test_agentd_resume_route_requires_confirmed_for_pending_action() -> None:
    service, _ = build_remediation_service(
        [inspect_workload("healthy"), inspect_pod(), scale_workload()]
    )
    app = AgentdApp(service)
    waiting = await service.run_goal(*remediation_goal_task())

    session = await app.handle(HttpRequest("GET", f"/v1/sessions/{waiting.result.session_id}"))
    response = await app.handle(
        HttpRequest("POST", f"/v1/sessions/{waiting.result.session_id}/resume")
    )

    assert session.status_code == 200
    pending_action = session.body["pending_action"]
    assert isinstance(pending_action, dict)
    assert pending_action["attempt"] == 1
    assert isinstance(pending_action["idempotency_key"], str)
    assert isinstance(pending_action["parameters_hash"], str)
    assert len(pending_action["parameters_hash"]) == 64
    assert response.status_code == 400
    assert response.body["error"] == {
        "code": "bad_request",
        "message": "resume requires boolean confirmed for pending action",
    }


@pytest.mark.asyncio
async def test_agentd_routes_return_404_and_405_errors() -> None:
    service, _ = build_service([])
    app = AgentdApp(service)

    missing_route = await app.handle(HttpRequest("GET", "/v1/missing"))
    wrong_method = await app.handle(HttpRequest("POST", "/health"))
    missing_session = await app.handle(HttpRequest("GET", "/v1/sessions/session-missing"))
    missing_resume_session = await app.handle(
        HttpRequest(
            "POST",
            "/v1/sessions/session-missing/resume",
            immutable_json({"confirmed": True}),
        )
    )
    missing_pause_session = await app.handle(
        HttpRequest("POST", "/v1/sessions/session-missing/pause")
    )
    missing_events_session = await app.handle(
        HttpRequest("GET", "/v1/sessions/session-missing/events")
    )
    missing_cancel_session = await app.handle(
        HttpRequest("POST", "/v1/sessions/session-missing/cancel")
    )

    assert missing_route.status_code == 404
    assert missing_route.body["error"] == {
        "code": "not_found",
        "message": "unknown route: /v1/missing",
    }
    assert wrong_method.status_code == 405
    assert wrong_method.headers["allow"] == "GET"
    assert wrong_method.body["error"] == {
        "code": "method_not_allowed",
        "message": "method is not allowed for this route",
    }
    assert missing_session.status_code == 404
    assert missing_session.body["error"] == {
        "code": "not_found",
        "message": "session not found: session-missing",
    }
    assert missing_resume_session.status_code == 404
    assert missing_resume_session.body["error"] == {
        "code": "not_found",
        "message": "session not found: session-missing",
    }
    assert missing_pause_session.status_code == 404
    assert missing_pause_session.body["error"] == {
        "code": "not_found",
        "message": "session not found: session-missing",
    }
    assert missing_events_session.status_code == 404
    assert missing_events_session.body["error"] == {
        "code": "not_found",
        "message": "session not found: session-missing",
    }
    assert missing_cancel_session.status_code == 404
    assert missing_cancel_session.body["error"] == {
        "code": "not_found",
        "message": "session not found: session-missing",
    }
