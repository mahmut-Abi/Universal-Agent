from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from universal_agent.core import DomainIdentity
from universal_agent.domain import DomainLoader, DomainRuntime, RuntimeBuilder, RuntimeComponents
from universal_agent.host.config import RuntimeConfig, StoreBackend
from universal_agent.model import ModelAdapter
from universal_agent.persistence import FileEventStore, FileSessionStore
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

    @classmethod
    def build(
        cls,
        *,
        config: RuntimeConfig,
        model: ModelAdapter,
        domain: DomainRuntime,
    ) -> RuntimeHost:
        config.validate()
        active_domain = DomainLoader().load(domain)
        identity = active_domain.identity
        _validate_domain_config(config, identity)
        components = RuntimeBuilder().build(active_domain)
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
        return cls(
            config=config,
            runtime_api=api,
            service=RuntimeService(runtime_api=api, components=components),
            components=components,
            domain_identity=identity,
        )


def _validate_domain_config(config: RuntimeConfig, identity: DomainIdentity) -> None:
    expected = config.domain
    if expected.name is not None and expected.name != identity.name:
        raise ValueError(f"configured domain {expected.name} does not match {identity.name}")
    if expected.version is not None and expected.version != identity.version:
        raise ValueError(
            f"configured domain version {expected.version} does not match {identity.version}"
        )


def _build_stores(config: RuntimeConfig) -> tuple[SessionStore, _EventStore]:
    if config.store.backend is StoreBackend.MEMORY:
        events = InMemoryEventSink()
        return InMemoryStateStore(), events
    if config.store.backend is StoreBackend.FILE:
        assert config.store.path is not None
        return FileSessionStore(config.store.path), FileEventStore(config.store.path)
    raise ValueError(f"unsupported store backend: {config.store.backend}")
