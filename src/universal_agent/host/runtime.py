from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from universal_agent.core import DomainIdentity
from universal_agent.distributed import (
    DistributedRuntimeCoordinator,
    FileDistributedLockRegistry,
    FileWorkerRegistry,
    FileWorkQueue,
    InMemoryDistributedLockRegistry,
    InMemoryWorkerRegistry,
    InMemoryWorkQueue,
    SQLiteWorkQueue,
)
from universal_agent.domain import (
    DomainComposition,
    DomainManager,
    DomainNotFoundError,
    DomainRuntime,
    RuntimeBuilder,
    RuntimeComponents,
)
from universal_agent.host.config import RuntimeConfig, StoreBackend
from universal_agent.model import ModelAdapter
from universal_agent.persistence import (
    FileEventStore,
    FileSessionStore,
    SQLiteEventStore,
    SQLiteSessionStore,
)
from universal_agent.profile import AgentProfile
from universal_agent.runtime import (
    AgentRuntime,
    EventReader,
    EventSink,
    InMemoryEventSink,
    RuntimeAPI,
)
from universal_agent.service import RuntimeService
from universal_agent.state import InMemoryStateStore, SessionStore


class _EventStore(EventSink, EventReader, Protocol):
    pass


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
    profile: AgentProfile | None = None

    @classmethod
    def build(
        cls,
        *,
        config: RuntimeConfig,
        model: ModelAdapter,
        domain: DomainRuntime,
        profile: AgentProfile | None = None,
    ) -> RuntimeHost:
        return cls.build_composed(
            config=config,
            model=model,
            domains=(domain,),
            profile=profile,
        )

    @classmethod
    def build_composed(
        cls,
        *,
        config: RuntimeConfig,
        model: ModelAdapter,
        domains: tuple[DomainRuntime, ...],
        profile: AgentProfile | None = None,
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
        components = RuntimeBuilder().build(composition)
        session_store, event_store = _build_stores(config)
        runtime = AgentRuntime(
            model=model,
            state_store=session_store,
            components=components,
            event_sink=event_store,
            max_iterations=config.limits.max_iterations,
            max_recovery_steps=config.limits.max_recovery_steps,
            environment=config.environment,
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
                distributed_coordinator=distributed_coordinator,
            ),
            components=components,
            domain_identity=identity,
            domain_identities=composition.identities,
            domain_composition=composition,
            distributed_coordinator=distributed_coordinator,
            profile=profile,
        )

    @classmethod
    def from_profile(
        cls,
        *,
        profile: AgentProfile,
        model: ModelAdapter,
        domain: DomainRuntime,
    ) -> RuntimeHost:
        return cls.build(
            config=profile.runtime,
            model=model,
            domain=domain,
            profile=profile,
        )

    @classmethod
    def from_profile_composed(
        cls,
        *,
        profile: AgentProfile,
        model: ModelAdapter,
        domains: tuple[DomainRuntime, ...],
    ) -> RuntimeHost:
        return cls.build_composed(
            config=profile.runtime,
            model=model,
            domains=domains,
            profile=profile,
        )


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
        return FileSessionStore(config.store.path), FileEventStore(config.store.path)
    if config.store.backend is StoreBackend.SQLITE:
        assert config.store.path is not None
        return SQLiteSessionStore(config.store.path), SQLiteEventStore(config.store.path)
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
    raise ValueError(f"unsupported distributed locks backend: {config.distributed_locks.backend}")


def _build_worker_registry(config: RuntimeConfig) -> InMemoryWorkerRegistry:
    if config.distributed_workers.backend is StoreBackend.MEMORY:
        return InMemoryWorkerRegistry()
    if config.distributed_workers.backend is StoreBackend.FILE:
        assert config.distributed_workers.path is not None
        return FileWorkerRegistry(config.distributed_workers.path)
    raise ValueError(
        f"unsupported distributed workers backend: {config.distributed_workers.backend}"
    )


def _format_identities(identities: tuple[DomainIdentity, ...]) -> str:
    return ", ".join(f"{item.name}@{item.version}" for item in identities)
