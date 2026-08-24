from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from universal_agent.context import DomainContextProvider
from universal_agent.core import (
    AgentState,
    CapabilityDefinition,
    ContextFragment,
    DomainIdentity,
    DomainManifest,
    DomainMetadata,
    JsonValue,
)
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import EvidenceExtractor
from universal_agent.memory import MemoryKind, MemoryRecord
from universal_agent.policy import Policy
from universal_agent.recovery import RecoveryRule, RecoveryStrategy
from universal_agent.tasks import TaskExpander
from universal_agent.tools import Tool
from universal_agent.world import WorldUpdater


class DomainRuntime(Protocol):
    @property
    def manifest(self) -> DomainManifest: ...

    def capabilities(self) -> tuple[CapabilityDefinition, ...]: ...

    def tools(self) -> tuple[Tool, ...]: ...

    def policies(self) -> tuple[Policy, ...]: ...

    def evaluators(self) -> tuple[Evaluator, ...]: ...

    def context_providers(self) -> tuple[DomainContextProvider, ...]: ...

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]: ...

    def world_updaters(self) -> tuple[WorldUpdater, ...]: ...

    def task_expanders(self) -> tuple[TaskExpander, ...]: ...

    def recovery_rules(self) -> tuple[RecoveryRule, ...]: ...

    def memories(self) -> tuple[MemoryRecord, ...]: ...


@dataclass(frozen=True, slots=True)
class ActiveDomain:
    manifest: DomainManifest
    capabilities: tuple[CapabilityDefinition, ...]
    tools: tuple[Tool, ...]
    policies: tuple[Policy, ...]
    evaluators: tuple[Evaluator, ...]
    context_providers: tuple[DomainContextProvider, ...]
    evidence_extractors: tuple[EvidenceExtractor, ...]
    world_updaters: tuple[WorldUpdater, ...]
    task_expanders: tuple[TaskExpander, ...]
    recovery_rules: tuple[RecoveryRule, ...]
    memories: tuple[MemoryRecord, ...]

    @property
    def identity(self) -> DomainIdentity:
        metadata = self.manifest.metadata
        return DomainIdentity(metadata.name, metadata.version)


@dataclass(frozen=True, slots=True)
class DomainComposition:
    """A validated set of active domains for one runtime.

    This is deliberately conservative: capability and tool names must be unique
    across domains until the kernel has first-class namespaced decisions.
    """

    domains: tuple[ActiveDomain, ...]

    def __post_init__(self) -> None:
        if not self.domains:
            raise DomainValidationError("domain composition requires at least one domain")
        self._validate_unique_identities()
        self._validate_unique_capabilities()
        self._validate_unique_tools()

    @classmethod
    def single(cls, domain: ActiveDomain) -> DomainComposition:
        return cls((domain,))

    @property
    def primary(self) -> ActiveDomain:
        return self.domains[0]

    @property
    def identities(self) -> tuple[DomainIdentity, ...]:
        return tuple(domain.identity for domain in self.domains)

    @property
    def scope(self) -> str | None:
        if len(self.domains) == 1:
            return self.primary.manifest.metadata.name
        return None

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return tuple(item for domain in self.domains for item in domain.capabilities)

    def tools(self) -> tuple[Tool, ...]:
        return tuple(item for domain in self.domains for item in domain.tools)

    def policies(self) -> tuple[Policy, ...]:
        return tuple(item for domain in self.domains for item in domain.policies)

    def evaluators(self) -> tuple[Evaluator, ...]:
        return tuple(item for domain in self.domains for item in domain.evaluators)

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return tuple(
            _NamespacedContextProvider(domain.identity, provider)
            for domain in self.domains
            for provider in domain.context_providers
        )

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
        return tuple(item for domain in self.domains for item in domain.evidence_extractors)

    def evidence_extractors_for(self, identity: DomainIdentity) -> tuple[EvidenceExtractor, ...]:
        domain = self.domain_for(identity)
        return () if domain is None else domain.evidence_extractors

    def world_updaters(self) -> tuple[WorldUpdater, ...]:
        return tuple(item for domain in self.domains for item in domain.world_updaters)

    def world_updaters_for(self, identity: DomainIdentity) -> tuple[WorldUpdater, ...]:
        domain = self.domain_for(identity)
        return () if domain is None else domain.world_updaters

    def task_expanders(self) -> tuple[TaskExpander, ...]:
        return tuple(item for domain in self.domains for item in domain.task_expanders)

    def task_expanders_for(self, identity: DomainIdentity) -> tuple[TaskExpander, ...]:
        domain = self.domain_for(identity)
        return () if domain is None else domain.task_expanders

    def recovery_rules(self) -> tuple[RecoveryRule, ...]:
        return tuple(item for domain in self.domains for item in domain.recovery_rules)

    def memories(self) -> tuple[MemoryRecord, ...]:
        return tuple(item for domain in self.domains for item in domain.memories)

    def evaluator_names(self) -> tuple[str, ...]:
        return tuple(name for domain in self.domains for name in domain.manifest.evaluator_names)

    def evaluator_names_for(self, identity: DomainIdentity) -> tuple[str, ...]:
        domain = self.domain_for(identity)
        return () if domain is None else domain.manifest.evaluator_names

    def domain_for(self, identity: DomainIdentity) -> ActiveDomain | None:
        for domain in self.domains:
            if domain.identity == identity:
                return domain
        return None

    def _validate_unique_identities(self) -> None:
        seen: set[DomainIdentity] = set()
        duplicates: set[DomainIdentity] = set()
        for identity in self.identities:
            if identity in seen:
                duplicates.add(identity)
            seen.add(identity)
        if duplicates:
            names = ", ".join(f"{item.name}@{item.version}" for item in sorted(duplicates, key=str))
            raise DomainValidationError(f"duplicate domain identities: {names}")

    def _validate_unique_capabilities(self) -> None:
        owners: dict[str, DomainIdentity] = {}
        conflicts: list[str] = []
        for domain in self.domains:
            for capability in domain.capabilities:
                owner = owners.get(capability.name)
                if owner is not None:
                    conflicts.append(
                        f"{capability.name} ({owner.name}@{owner.version}, "
                        f"{domain.identity.name}@{domain.identity.version})"
                    )
                owners[capability.name] = domain.identity
        if conflicts:
            raise DomainValidationError(
                "domain composition contains duplicate capabilities: "
                + ", ".join(sorted(conflicts))
            )

    def _validate_unique_tools(self) -> None:
        owners: dict[str, DomainIdentity] = {}
        conflicts: list[str] = []
        for domain in self.domains:
            for tool in domain.tools:
                name = tool.definition.name
                owner = owners.get(name)
                if owner is not None:
                    conflicts.append(
                        f"{name} ({owner.name}@{owner.version}, "
                        f"{domain.identity.name}@{domain.identity.version})"
                    )
                owners[name] = domain.identity
        if conflicts:
            raise DomainValidationError(
                "domain composition contains duplicate tools: " + ", ".join(sorted(conflicts))
            )


@dataclass(frozen=True, slots=True)
class _NamespacedContextProvider:
    identity: DomainIdentity
    provider: DomainContextProvider

    @property
    def name(self) -> str:
        return f"{self.identity.name}.{self.provider.name}"

    def provide(self, state: AgentState) -> tuple[ContextFragment, ...]:
        return tuple(
            ContextFragment(
                f"{self.identity.name}.{fragment.key}",
                fragment.content,
                fragment.priority,
            )
            for fragment in self.provider.provide(state)
        )


class DomainValidationError(ValueError):
    pass


class DomainLoader:
    def load(self, domain: DomainRuntime) -> ActiveDomain:
        manifest = domain.manifest
        capabilities = domain.capabilities()
        tools = domain.tools()
        policies = domain.policies()
        evaluators = domain.evaluators()
        providers = domain.context_providers()
        extractors = domain.evidence_extractors()
        updaters = domain.world_updaters()
        expanders = domain.task_expanders()
        recovery_rules = domain.recovery_rules()
        memories = domain.memories()
        self._validate(
            manifest,
            capabilities,
            tools,
            evaluators,
            expanders,
            recovery_rules,
            memories,
        )
        return ActiveDomain(
            manifest,
            capabilities,
            tools,
            policies,
            evaluators,
            providers,
            extractors,
            updaters,
            expanders,
            recovery_rules,
            memories,
        )

    def _validate(
        self,
        manifest: DomainManifest,
        capabilities: tuple[CapabilityDefinition, ...],
        tools: tuple[Tool, ...],
        evaluators: tuple[Evaluator, ...],
        expanders: tuple[TaskExpander, ...],
        recovery_rules: tuple[RecoveryRule, ...],
        memories: tuple[MemoryRecord, ...],
    ) -> None:
        if manifest.api_version != "agent.nantian.dev/v1alpha1" or manifest.kind != "Domain":
            raise DomainValidationError("unsupported domain apiVersion or kind")
        if not manifest.metadata.name or not manifest.metadata.version:
            raise DomainValidationError("domain name and version are required")
        capability_names = {item.name for item in capabilities}
        if len(capability_names) != len(capabilities):
            raise DomainValidationError("domain contains duplicate capabilities")
        if capability_names != set(manifest.capability_names):
            raise DomainValidationError("manifest capability references do not match registrations")
        evaluator_names = {item.name for item in evaluators}
        if not evaluator_names:
            raise DomainValidationError("domain requires at least one evaluator")
        if not manifest.evaluator_names:
            raise DomainValidationError("domain manifest requires at least one evaluator")
        if evaluator_names != set(manifest.evaluator_names):
            raise DomainValidationError("manifest evaluator references do not match registrations")
        for tool in tools:
            unknown = set(tool.definition.capabilities) - capability_names
            if unknown:
                names = ", ".join(sorted(unknown))
                raise DomainValidationError(
                    f"tool {tool.definition.name} references unknown capabilities: {names}"
                )
        for expander in expanders:
            unknown = set(expander.capability_names) - capability_names
            if unknown:
                names = ", ".join(sorted(unknown))
                raise DomainValidationError(
                    f"task expander {expander.name} references unknown capabilities: {names}"
                )
        for rule in recovery_rules:
            if (
                rule.strategy is RecoveryStrategy.ALTERNATIVE_CAPABILITY
                and rule.capability not in capability_names
            ):
                raise DomainValidationError(
                    f"recovery rule {rule.name} references unknown capability: {rule.capability}"
                )
        for record in memories:
            if record.kind is MemoryKind.EPISODIC:
                raise DomainValidationError(
                    "domain may not declare episodic memory; episodic records are "
                    "written only by the runtime at a terminal transition"
                )

    def manifest_from_json(self, path: Path) -> DomainManifest:
        raw = cast(dict[str, JsonValue], json.loads(path.read_text(encoding="utf-8")))
        try:
            metadata = cast(dict[str, JsonValue], raw["metadata"])
            specification = cast(dict[str, JsonValue], raw["spec"])
            capabilities = cast(list[JsonValue], specification["capabilities"])
            evaluators = cast(list[JsonValue], specification["evaluators"])
            ontology = cast(list[JsonValue], specification.get("ontology", []))
            return DomainManifest(
                api_version=str(raw["apiVersion"]),
                kind=str(raw["kind"]),
                metadata=DomainMetadata(
                    name=str(metadata["name"]),
                    version=str(metadata["version"]),
                    description=str(metadata.get("description", "")),
                ),
                ontology=tuple(str(item) for item in ontology),
                capability_names=tuple(str(item) for item in capabilities),
                evaluator_names=tuple(str(item) for item in evaluators),
            )
        except (KeyError, TypeError) as exc:
            raise DomainValidationError("invalid domain manifest JSON") from exc
