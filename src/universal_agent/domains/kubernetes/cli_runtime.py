from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

from pydantic import PositiveFloat

from universal_agent.core import (
    Decision,
    DecisionContext,
    DecisionType,
    JsonMapping,
    JsonValue,
    immutable_json,
)
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticNonEmptyString,
    parse_payload,
)
from universal_agent.distributed import DistributedRuntimeCoordinator
from universal_agent.domain import DomainLoader, RuntimeBuilder
from universal_agent.domains.kubernetes.api import KubernetesApiBackend
from universal_agent.domains.kubernetes.backend import KubernetesBackend, KubernetesMutationBackend
from universal_agent.domains.kubernetes.domain import KubernetesRemediationDomain
from universal_agent.domains.kubernetes.kubectl import KubectlBackend
from universal_agent.host import (
    DomainConfig,
    RuntimeConfig,
    RuntimeHost,
    build_configured_model_adapter,
)
from universal_agent.model import ModelAdapter, ModelUsage, ScriptedModelAdapter
from universal_agent.profile import AgentProfile, ProfileConfig
from universal_agent.runtime import AgentRuntime, InMemoryEventSink, RuntimeAPI
from universal_agent.security import EnvSecretProvider, SecretProvider, resolve_secret_value
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore

LOCAL_PROFILE_NAME = "local-kubernetes"
PREFLIGHT_CAPABILITIES = (
    "inspect_cluster",
    "inspect_workload",
    "inspect_pod",
    "inspect_logs",
    "inspect_events",
    "scale_workload",
)

ModelAdapterBuilder = Callable[..., ModelAdapter]


class _KubernetesBackendSettingsPayload(ConfigPayload):
    default_namespace: PydanticNonEmptyString = "default"
    context: PydanticNonEmptyString | None = None
    kubeconfig: PydanticNonEmptyString | None = None
    timeout_seconds: PositiveFloat = 10.0
    api_server: PydanticNonEmptyString | None = None
    bearer_token_secret: PydanticNonEmptyString | None = None


def local_domain() -> DomainConfig:
    return DomainConfig("kubernetes", "0.2.0")


def default_decisions() -> tuple[Decision, ...]:
    return (
        Decision(
            DecisionType.EXECUTE,
            "Inspect workload from local CLI profile",
            capability="inspect_workload",
            target="deployment/example",
            arguments=immutable_json({"name": "example"}),
            expected_observations=("healthy",),
        ),
        Decision(DecisionType.FINISH, "Local CLI profile verified workload health"),
        Decision(
            DecisionType.EXECUTE,
            "Attempt invalid scale from local CLI evaluation profile",
            capability="scale_workload",
            target="deployment/example",
            arguments=immutable_json({"name": "example", "namespace": "default", "replicas": 0}),
            expected_observations=("mutation_applied",),
        ),
    )


class KubernetesRemediationDecisionAdapter:
    """Workload-aware scripted decision adapter for the kubernetes run flow.

    Derives the inspection target from the goal/task description, so
    ``agent kubernetes run --workload X`` inspects the requested workload
    instead of a fixture default.
    """

    def __init__(self) -> None:
        self._inspect_used = False
        self.contexts: list[DecisionContext] = []

    async def decide(self, context: DecisionContext) -> Decision:
        self.contexts.append(context)
        workload, namespace = self._workload_and_namespace(context)
        if not self._inspect_used:
            self._inspect_used = True
            arguments: dict[str, JsonValue] = {"name": workload}
            if namespace:
                arguments["namespace"] = namespace
            return Decision(
                DecisionType.EXECUTE,
                f"Inspect Kubernetes workload {workload}",
                capability="inspect_workload",
                target=f"deployment/{workload}",
                arguments=immutable_json(arguments),
                expected_observations=("healthy",),
            )
        return Decision(
            DecisionType.FINISH,
            f"Kubernetes workload {workload} inspected",
        )

    def model_usage(self) -> ModelUsage | None:
        return None

    @staticmethod
    def _workload_and_namespace(
        context: DecisionContext,
    ) -> tuple[str, str | None]:
        import re

        for text in (context.task_description, context.goal_description):
            workload = re.search(r"deployment/([A-Za-z0-9._-]+)", text)
            if not workload:
                continue
            namespace = re.search(r"namespace ([A-Za-z0-9._-]+)", text)
            return (
                workload.group(1),
                namespace.group(1) if namespace else None,
            )
        return "example", None


def default_profile() -> AgentProfile:
    domain = local_domain()
    return AgentProfile(
        LOCAL_PROFILE_NAME,
        "0.1.0",
        "Local fake-backed Kubernetes profile",
        domain,
        RuntimeConfig(environment=immutable_json({"environment": "local"}), domain=domain),
        (domain,),
    )


class DefaultKubernetesCliBackend:
    async def inspect(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        resource = arguments.get("name", "example")
        return immutable_json(
            {
                "resource": f"deployment/{resource}",
                "healthy": True,
                "capability": capability,
            }
        )

    async def mutate(self, capability: str, arguments: JsonMapping) -> JsonMapping:
        resource = arguments.get("name", "example")
        return immutable_json(
            {
                "resource": f"deployment/{resource}",
                "mutation_applied": True,
                "capability": capability,
            }
        )


def build_default_service() -> RuntimeService:
    backend = DefaultKubernetesCliBackend()
    profile = default_profile()
    components = RuntimeBuilder().build(
        DomainLoader().load(KubernetesRemediationDomain(backend, backend))
    )
    store = InMemoryStateStore()
    events = InMemoryEventSink()
    runtime = AgentRuntime(
        model=KubernetesRemediationDecisionAdapter(),
        state_store=store,
        components=components,
        event_sink=events,
        environment=immutable_json({"environment": "local"}),
    )
    return RuntimeService(
        runtime_api=RuntimeAPI(runtime=runtime, session_store=store, event_reader=events),
        components=components,
        profiles=(profile,),
        config=profile.runtime,
        distributed_coordinator=DistributedRuntimeCoordinator(),
    )


def build_configured_service(
    profile_config_path: str | Path,
    *,
    model_adapter_builder: ModelAdapterBuilder = build_configured_model_adapter,
) -> RuntimeService:
    profile = ProfileConfig.from_json_file(profile_config_path).to_profile()
    secret_provider = EnvSecretProvider()
    backend = configured_kubernetes_backend(
        profile.runtime.configured_domains() or (profile.domain,),
        config=profile.runtime,
        secret_provider=secret_provider,
    )
    host = RuntimeHost.from_profile(
        profile=profile,
        model=(
            KubernetesRemediationDecisionAdapter()
            if profile.runtime.model.provider == "scripted"
            else model_adapter_builder(
                profile.runtime,
                scripted_decisions=default_decisions(),
                secret_provider=secret_provider,
            )
        ),
        domain=KubernetesRemediationDomain(
            cast(KubernetesBackend, backend),
            cast(KubernetesMutationBackend, backend),
        ),
        secret_provider=secret_provider,
    )
    return host.service


def build_configured_probe_service(profile_config_path: str | Path) -> RuntimeService:
    """Build RuntimeService metadata without requiring the configured model to connect."""

    profile = ProfileConfig.from_json_file(profile_config_path).to_profile()
    secret_provider = EnvSecretProvider()
    backend = configured_kubernetes_backend(
        profile.runtime.configured_domains() or (profile.domain,),
        config=profile.runtime,
        secret_provider=secret_provider,
    )
    host = RuntimeHost.from_profile(
        profile=profile,
        model=ScriptedModelAdapter(default_decisions()),
        domain=KubernetesRemediationDomain(
            cast(KubernetesBackend, backend),
            cast(KubernetesMutationBackend, backend),
        ),
        secret_provider=secret_provider,
    )
    return host.service


def configured_kubernetes_backend(
    domains: tuple[DomainConfig, ...],
    *,
    config: RuntimeConfig | None = None,
    secret_provider: SecretProvider | None = None,
) -> object:
    if not domains:
        return DefaultKubernetesCliBackend()
    primary = domains[0]
    backend = primary.backend or "fake"
    if backend == "fake":
        return DefaultKubernetesCliBackend()
    settings = _kubernetes_backend_settings(primary.settings)
    if backend == "kubectl":
        return KubectlBackend(
            default_namespace=settings.default_namespace,
            context=settings.context,
            kubeconfig=settings.kubeconfig,
            timeout_seconds=settings.timeout_seconds,
        )
    if backend == "kubernetes_api":
        if settings.api_server is None:
            raise ValueError("domain setting api_server must be a non-empty string")
        return KubernetesApiBackend(
            api_server=settings.api_server,
            bearer_token=configured_kubernetes_api_token(
                settings.bearer_token_secret,
                config=config,
                secret_provider=secret_provider,
            ),
            default_namespace=settings.default_namespace,
            timeout_seconds=settings.timeout_seconds,
        )
    raise ValueError(f"unsupported Kubernetes domain backend: {backend}")


def profile_domain_config(
    *,
    domain_backend: str,
    kubectl_namespace: str,
    kubectl_context: str | None,
    kubectl_kubeconfig: str | None,
    kubectl_timeout_seconds: float,
    kubernetes_api_server: str | None,
    kubernetes_api_namespace: str,
    kubernetes_api_token_secret: str | None,
    kubernetes_api_timeout_seconds: float,
) -> dict[str, object]:
    domain: dict[str, object] = {"name": "kubernetes", "version": "0.2.0"}
    if domain_backend == "fake":
        return domain
    if domain_backend == "kubectl":
        settings: dict[str, object] = {
            "default_namespace": kubectl_namespace,
            "timeout_seconds": kubectl_timeout_seconds,
        }
        if kubectl_context:
            settings["context"] = kubectl_context
        if kubectl_kubeconfig:
            settings["kubeconfig"] = kubectl_kubeconfig
        domain["backend"] = "kubectl"
        domain["settings"] = settings
        return domain
    if domain_backend == "kubernetes_api":
        if kubernetes_api_server is None or not kubernetes_api_server.strip():
            raise ValueError("kubernetes_api backend requires --kubernetes-api-server")
        settings = {
            "api_server": kubernetes_api_server,
            "default_namespace": kubernetes_api_namespace,
            "timeout_seconds": kubernetes_api_timeout_seconds,
        }
        if kubernetes_api_token_secret is not None:
            settings["bearer_token_secret"] = kubernetes_api_token_secret
        domain["backend"] = "kubernetes_api"
        domain["settings"] = settings
        return domain
    raise ValueError(f"unsupported domain backend: {domain_backend}")


def _kubernetes_backend_settings(settings: JsonMapping) -> _KubernetesBackendSettingsPayload:
    try:
        return parse_payload(_KubernetesBackendSettingsPayload, settings)
    except ValueError as exc:
        raise ValueError(f"invalid Kubernetes domain settings: {exc}") from exc


def configured_kubernetes_api_token(
    secret_name: str | None,
    *,
    config: RuntimeConfig | None,
    secret_provider: SecretProvider | None,
) -> str | None:
    if secret_name is None:
        return None
    if config is None:
        raise ValueError("kubernetes_api bearer_token_secret requires runtime config")
    for secret in config.secrets:
        if secret.name == secret_name:
            return resolve_secret_value(secret, provider=secret_provider)
    raise ValueError(f"domain setting bearer_token_secret is not declared: {secret_name}")
