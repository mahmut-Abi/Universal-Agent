from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from universal_agent.core import (
    Decision,
    DomainIdentity,
    JsonMapping,
    immutable_json,
)
from universal_agent.distributed import (
    DistributedRuntimeCoordinator,
    FileDistributedLockRegistry,
    FileWorkerRegistry,
    FileWorkQueue,
    InMemoryDistributedLockRegistry,
    InMemoryWorkerRegistry,
    InMemoryWorkQueue,
    SQLiteDistributedLockRegistry,
    SQLiteWorkerRegistry,
    SQLiteWorkQueue,
)
from universal_agent.domain import (
    DomainComposition,
    DomainManager,
    DomainNotFoundError,
    DomainPackageRegistry,
    DomainPackageRuntimeActivation,
    DomainRuntime,
    DomainRuntimeLoadContext,
    RuntimeBuilder,
    RuntimeComponents,
    load_domain_package,
    load_domain_package_runtime,
)
from universal_agent.host.config import DomainConfig, ModelProvider, RuntimeConfig, StoreBackend
from universal_agent.model import (
    JsonHttpModelAdapter,
    JsonHttpModelTransport,
    ModelAdapter,
    ModelUsage,
    OpenAIChatCompletionsModelAdapter,
    OpenAIModelTransport,
    OpenAIResponsesModelAdapter,
    ScriptedModelAdapter,
)
from universal_agent.persistence import FileRuntimeStore, SQLiteRuntimeStore
from universal_agent.profile import AgentProfile
from universal_agent.runtime import (
    AgentRuntime,
    EventReader,
    EventSink,
    InMemoryEventSink,
    RuntimeAPI,
)
from universal_agent.security import (
    SecretProvider,
    SecretResolutionReport,
    resolve_secret_refs,
    resolve_secret_value,
)
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore, SessionStore
from universal_agent.state.event_store import EventStore


class _EventStore(EventSink, EventReader, EventStore, Protocol):
    pass


def build_configured_model_adapter(
    config: RuntimeConfig,
    *,
    scripted_decisions: Iterable[Decision] = (),
    scripted_usage: Iterable[ModelUsage] = (),
    secret_provider: SecretProvider | None = None,
    json_http_transport: JsonHttpModelTransport | None = None,
    openai_transport: OpenAIModelTransport | None = None,
) -> ModelAdapter:
    """Build a ModelAdapter from RuntimeConfig without exposing secret values.

    RuntimeConfig stores only model metadata and optional secret reference names.
    The actual secret value is resolved at the host boundary and passed directly
    to the adapter; it is never written into config projections.
    """

    config.validate()
    if config.model.provider is ModelProvider.SCRIPTED:
        return ScriptedModelAdapter(scripted_decisions, usage=scripted_usage)
    if config.model.provider is ModelProvider.JSON_HTTP:
        assert config.model.endpoint is not None
        return JsonHttpModelAdapter(
            config.model.endpoint,
            config.model.name,
            provider=config.model.provider.value,
            api_key=_configured_model_api_key(config, secret_provider),
            extra_headers=_configured_model_headers(config),
            timeout_seconds=config.model.timeout_seconds,
            transport=json_http_transport,
        )
    if config.model.provider is ModelProvider.OPENAI_RESPONSES:
        api_key = _configured_model_api_key(config, secret_provider)
        if api_key is None:
            raise ValueError("openai_responses model requires resolved api_key_secret")
        return OpenAIResponsesModelAdapter(
            config.model.name,
            api_key=api_key,
            endpoint=config.model.endpoint or OpenAIResponsesModelAdapter.DEFAULT_ENDPOINT,
            extra_headers=_configured_model_headers(config),
            timeout_seconds=config.model.timeout_seconds,
            transport=openai_transport or json_http_transport,
        )
    if config.model.provider is ModelProvider.OPENAI_CHAT_COMPLETIONS:
        api_key = _configured_model_api_key(config, secret_provider)
        if api_key is None:
            raise ValueError("openai_chat_completions model requires resolved api_key_secret")
        return OpenAIChatCompletionsModelAdapter(
            config.model.name,
            api_key=api_key,
            endpoint=config.model.endpoint or OpenAIChatCompletionsModelAdapter.DEFAULT_ENDPOINT,
            extra_headers=_configured_model_headers(config),
            timeout_seconds=config.model.timeout_seconds,
            response_format=config.model.response_format or "json_schema",
            transport=openai_transport or json_http_transport,
        )
    raise ValueError(f"unsupported model provider: {config.model.provider}")


def _configured_model_api_key(
    config: RuntimeConfig,
    provider: SecretProvider | None,
) -> str | None:
    secret_name = config.model.api_key_secret
    if secret_name is None:
        return None
    return _configured_secret_value(config, secret_name, provider)


def _configured_secret_value(
    config: RuntimeConfig,
    secret_name: str,
    provider: SecretProvider | None,
) -> str | None:
    for secret in config.secrets:
        if secret.name == secret_name:
            return resolve_secret_value(secret, provider=provider)
    return None


def _configured_model_headers(config: RuntimeConfig) -> dict[str, str]:
    return {key: str(value) for key, value in config.model.headers.items()}


@dataclass(frozen=True, slots=True)
class RuntimeHost:
    """Application host that assembles the runtime from typed configuration.

    The Kernel stays behind AgentRuntime. Applications can build a RuntimeAPI or
    RuntimeService from one host instead of duplicating store, domain and
    runtime wiring.
    """

    config: RuntimeConfig
    runtime_api: RuntimeAPI
    service: RuntimeService
    components: RuntimeComponents
    domain_identity: DomainIdentity
    domain_identities: tuple[DomainIdentity, ...]
    domain_composition: DomainComposition
    distributed_coordinator: DistributedRuntimeCoordinator
    secret_resolution: SecretResolutionReport
    profile: AgentProfile | None = None

    @classmethod
    def build(
        cls,
        *,
        config: RuntimeConfig,
        model: ModelAdapter,
        domain: DomainRuntime,
        profile: AgentProfile | None = None,
        secret_provider: SecretProvider | None = None,
        domain_packages: DomainPackageRegistry | None = None,
    ) -> RuntimeHost:
        return cls.build_composed(
            config=config,
            model=model,
            domains=(domain,),
            profile=profile,
            secret_provider=secret_provider,
            domain_packages=domain_packages,
        )

    @classmethod
    def build_composed(
        cls,
        *,
        config: RuntimeConfig,
        model: ModelAdapter,
        domains: tuple[DomainRuntime, ...],
        profile: AgentProfile | None = None,
        secret_provider: SecretProvider | None = None,
        domain_packages: DomainPackageRegistry | None = None,
    ) -> RuntimeHost:
        if not domains:
            raise ValueError("runtime host requires at least one domain")
        config.validate()
        manager = DomainManager(domains)
        requested = tuple(domain.identity() for domain in config.configured_domains())
        composition = _activate_composition(manager, requested)
        identity = composition.primary.identity
        _validate_domain_config(config, composition.identities)
        _validate_profile(profile, composition.identities)
        secret_resolution = resolve_secret_refs(config.secrets, provider=secret_provider)
        components = RuntimeBuilder().build(composition)
        session_store, event_store = _build_stores(config)
        runtime = AgentRuntime(
            model=model,
            state_store=session_store,
            components=components,
            event_sink=event_store,
            event_store=event_store,
            max_iterations=config.limits.max_iterations,
            max_recovery_steps=config.limits.max_recovery_steps,
            max_total_cost_micros=config.limits.max_total_cost_micros,
            max_total_tokens=config.limits.max_total_tokens,
            environment=config.environment,
            secret_provider=secret_provider,
            secret_resolution=secret_resolution,
        )
        api = RuntimeAPI(
            runtime=runtime,
            session_store=session_store,
            event_reader=event_store,
        )
        distributed_coordinator = DistributedRuntimeCoordinator(
            queue=_build_work_queue(config),
            locks=_build_distributed_locks(config),
            workers=_build_worker_registry(config),
        )
        return cls(
            config=config,
            runtime_api=api,
            service=RuntimeService(
                runtime_api=api,
                components=components,
                profiles=() if profile is None else (profile,),
                config=config,
                secret_resolution=secret_resolution,
                distributed_coordinator=distributed_coordinator,
                domain_packages=domain_packages,
            ),
            components=components,
            domain_identity=identity,
            domain_identities=composition.identities,
            domain_composition=composition,
            distributed_coordinator=distributed_coordinator,
            secret_resolution=secret_resolution,
            profile=profile,
        )

    @classmethod
    def from_profile(
        cls,
        *,
        profile: AgentProfile,
        model: ModelAdapter,
        domain: DomainRuntime,
        secret_provider: SecretProvider | None = None,
        domain_packages: DomainPackageRegistry | None = None,
    ) -> RuntimeHost:
        return cls.build(
            config=profile.runtime,
            model=model,
            domain=domain,
            profile=profile,
            secret_provider=secret_provider,
            domain_packages=domain_packages,
        )

    @classmethod
    def from_profile_composed(
        cls,
        *,
        profile: AgentProfile,
        model: ModelAdapter,
        domains: tuple[DomainRuntime, ...],
        secret_provider: SecretProvider | None = None,
        domain_packages: DomainPackageRegistry | None = None,
    ) -> RuntimeHost:
        return cls.build_composed(
            config=profile.runtime,
            model=model,
            domains=domains,
            profile=profile,
            secret_provider=secret_provider,
            domain_packages=domain_packages,
        )

    @classmethod
    def from_configured_domain_packages(
        cls,
        *,
        config: RuntimeConfig,
        model: ModelAdapter,
        profile: AgentProfile | None = None,
        secret_provider: SecretProvider | None = None,
        verify_paths: bool = True,
    ) -> RuntimeHost:
        """Build a Host by activating DomainRuntime code declared in config."""

        activations = _load_configured_domain_package_runtimes(
            config,
            secret_provider=secret_provider,
            verify_paths=verify_paths,
        )
        return cls.build_composed(
            config=config,
            model=model,
            domains=tuple(activation.runtime for activation in activations),
            profile=profile,
            secret_provider=secret_provider,
            domain_packages=DomainPackageRegistry(
                tuple(activation.package for activation in activations)
            ),
        )


def _load_configured_domain_package_runtimes(
    config: RuntimeConfig,
    *,
    secret_provider: SecretProvider | None,
    verify_paths: bool,
) -> tuple[DomainPackageRuntimeActivation, ...]:
    if not config.domain_package_paths:
        raise ValueError("runtime config requires domain_package_paths to load Domain packages")
    config.validate()
    configured = {
        domain.identity(): domain
        for domain in config.configured_domains()
        if domain.name is not None and domain.version is not None
    }
    activations: list[DomainPackageRuntimeActivation] = []
    for package_path in config.domain_package_paths:
        package = load_domain_package(Path(package_path))
        domain_config = configured.get(package.identity)
        activations.append(
            load_domain_package_runtime(
                package,
                context=DomainRuntimeLoadContext(
                    identity=package.identity,
                    backend=None if domain_config is None else domain_config.backend,
                    settings=_domain_settings(domain_config),
                    environment=config.environment,
                    resolve_secret=lambda name: _configured_secret_value(
                        config,
                        name,
                        secret_provider,
                    ),
                ),
                verify_paths=verify_paths,
            )
        )
    return tuple(activations)


def _domain_settings(domain_config: DomainConfig | None) -> JsonMapping:
    if domain_config is None:
        return immutable_json()
    return domain_config.settings


def _validate_domain_config(
    config: RuntimeConfig,
    identities: tuple[DomainIdentity, ...],
) -> None:
    expected = tuple(domain.identity() for domain in config.configured_domains())
    if expected and expected != identities:
        raise ValueError(
            "configured domains "
            f"{_format_identities(expected)} do not match {_format_identities(identities)}"
        )


def _activate_composition(
    manager: DomainManager,
    requested: tuple[DomainIdentity, ...],
) -> DomainComposition:
    try:
        return manager.activate(requested or None).composition
    except DomainNotFoundError as exc:
        registered = manager.identities()
        if len(requested) == 1 and len(registered) == 1:
            _raise_single_domain_mismatch(requested[0], registered[0], exc)
        raise


def _raise_single_domain_mismatch(
    expected: DomainIdentity,
    actual: DomainIdentity,
    cause: Exception,
) -> None:
    if expected.name != actual.name:
        raise ValueError(
            f"configured domain {expected.name} does not match {actual.name}"
        ) from cause
    if expected.version != actual.version:
        raise ValueError(
            f"configured domain version {expected.version} does not match {actual.version}"
        ) from cause
    raise cause


def _validate_profile(
    profile: AgentProfile | None,
    identities: tuple[DomainIdentity, ...],
) -> None:
    if profile is None:
        return
    expected = tuple(domain.identity() for domain in profile.configured_domains())
    if expected != identities:
        raise ValueError(
            "profile domains "
            f"{_format_identities(expected)} do not match {_format_identities(identities)}"
        )


def _build_stores(config: RuntimeConfig) -> tuple[SessionStore, _EventStore]:
    if config.store.backend is StoreBackend.MEMORY:
        events = InMemoryEventSink()
        return InMemoryStateStore(), events
    if config.store.backend is StoreBackend.FILE:
        assert config.store.path is not None
        file_store = FileRuntimeStore(config.store.path)
        return file_store, file_store
    if config.store.backend is StoreBackend.SQLITE:
        assert config.store.path is not None
        sqlite_store = SQLiteRuntimeStore(config.store.path)
        return sqlite_store, sqlite_store
    raise ValueError(f"unsupported store backend: {config.store.backend}")


def _build_work_queue(config: RuntimeConfig) -> InMemoryWorkQueue:
    if config.distributed_queue.backend is StoreBackend.MEMORY:
        return InMemoryWorkQueue()
    if config.distributed_queue.backend is StoreBackend.FILE:
        assert config.distributed_queue.path is not None
        return FileWorkQueue(config.distributed_queue.path)
    if config.distributed_queue.backend is StoreBackend.SQLITE:
        assert config.distributed_queue.path is not None
        return SQLiteWorkQueue(config.distributed_queue.path)
    raise ValueError(f"unsupported distributed queue backend: {config.distributed_queue.backend}")


def _build_distributed_locks(config: RuntimeConfig) -> InMemoryDistributedLockRegistry:
    if config.distributed_locks.backend is StoreBackend.MEMORY:
        return InMemoryDistributedLockRegistry()
    if config.distributed_locks.backend is StoreBackend.FILE:
        assert config.distributed_locks.path is not None
        return FileDistributedLockRegistry(config.distributed_locks.path)
    if config.distributed_locks.backend is StoreBackend.SQLITE:
        assert config.distributed_locks.path is not None
        return SQLiteDistributedLockRegistry(config.distributed_locks.path)
    raise ValueError(f"unsupported distributed locks backend: {config.distributed_locks.backend}")


def _build_worker_registry(config: RuntimeConfig) -> InMemoryWorkerRegistry:
    if config.distributed_workers.backend is StoreBackend.MEMORY:
        return InMemoryWorkerRegistry()
    if config.distributed_workers.backend is StoreBackend.FILE:
        assert config.distributed_workers.path is not None
        return FileWorkerRegistry(config.distributed_workers.path)
    if config.distributed_workers.backend is StoreBackend.SQLITE:
        assert config.distributed_workers.path is not None
        return SQLiteWorkerRegistry(config.distributed_workers.path)
    raise ValueError(
        f"unsupported distributed workers backend: {config.distributed_workers.backend}"
    )


def _format_identities(identities: tuple[DomainIdentity, ...]) -> str:
    return ", ".join(f"{item.name}@{item.version}" for item in identities)
