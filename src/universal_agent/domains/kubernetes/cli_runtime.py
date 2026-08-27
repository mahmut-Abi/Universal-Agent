from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

from universal_agent.core import Decision, DecisionType, JsonMapping, JsonValue, immutable_json
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
from universal_agent.model import ModelAdapter, ScriptedModelAdapter
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
        model=ScriptedModelAdapter(default_decisions()),
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
        model=model_adapter_builder(
            profile.runtime,
            scripted_decisions=default_decisions(),
            secret_provider=secret_provider,
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
    if backend == "kubectl":
        return KubectlBackend(
            default_namespace=setting_string(
                primary.settings,
                "default_namespace",
                default="default",
            ),
            context=optional_setting_string(primary.settings, "context"),
            kubeconfig=optional_setting_string(primary.settings, "kubeconfig"),
            timeout_seconds=setting_float(primary.settings, "timeout_seconds", default=10.0),
        )
    if backend == "kubernetes_api":
        return KubernetesApiBackend(
            api_server=setting_string(primary.settings, "api_server", default=""),
            bearer_token=configured_kubernetes_api_token(
                primary.settings,
                config=config,
                secret_provider=secret_provider,
            ),
            default_namespace=setting_string(
                primary.settings,
                "default_namespace",
                default="default",
            ),
            timeout_seconds=setting_float(primary.settings, "timeout_seconds", default=10.0),
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


def setting_string(settings: JsonMapping, key: str, *, default: str) -> str:
    value = settings.get(key, default)
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError(f"domain setting {key} must be a non-empty string")


def optional_setting_string(settings: JsonMapping, key: str) -> str | None:
    value = settings.get(key)
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError(f"domain setting {key} must be a non-empty string")


def configured_kubernetes_api_token(
    settings: JsonMapping,
    *,
    config: RuntimeConfig | None,
    secret_provider: SecretProvider | None,
) -> str | None:
    secret_name = optional_setting_string(settings, "bearer_token_secret")
    if secret_name is None:
        return None
    if config is None:
        raise ValueError("kubernetes_api bearer_token_secret requires runtime config")
    for secret in config.secrets:
        if secret.name == secret_name:
            return resolve_secret_value(secret, provider=secret_provider)
    raise ValueError(f"domain setting bearer_token_secret is not declared: {secret_name}")


def setting_float(settings: JsonMapping, key: str, *, default: float) -> float:
    value: JsonValue = settings.get(key, default)
    if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
        return float(value)
    raise ValueError(f"domain setting {key} must be a positive number")
