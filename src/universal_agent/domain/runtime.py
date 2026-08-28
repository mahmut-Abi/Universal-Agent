from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from pydantic import Field

from universal_agent.context import DomainContextProvider
from universal_agent.core import (
    AgentState,
    CapabilityDefinition,
    ContextFragment,
    Decision,
    DomainIdentity,
    DomainManifest,
    DomainMetadata,
    Goal,
    JsonMapping,
    SessionId,
    Task,
    ToolDefinition,
    read_json_file,
)
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticNonEmptyString,
    duplicate_values,
    parse_json_object,
    parse_non_empty_string,
    parse_payload,
    parse_unique_non_empty_string_sequence,
)
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import EvidenceExtractor
from universal_agent.memory import MemoryKind, MemoryRecord
from universal_agent.policy import Policy
from universal_agent.recovery import RecoveryRule, RecoveryStrategy
from universal_agent.tasks import TaskExpander
from universal_agent.tools import Tool
from universal_agent.world import WorldSnapshot, WorldUpdater


@dataclass(frozen=True, slots=True)
class ActionArgumentContext:
    session_id: SessionId
    goal: Goal
    task: Task
    decision: Decision
    capability: CapabilityDefinition
    tool: ToolDefinition
    world: WorldSnapshot


class ActionArgumentProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def capability_names(self) -> tuple[str, ...]: ...

    def provide(self, context: ActionArgumentContext) -> JsonMapping: ...


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


class BaseDomainRuntime:
    """Convenience base class for Domain SDK implementations.

    Domain authors must provide the semantic contract that defines their Domain:
    manifest, capabilities, tools and evaluators. Optional runtime extension
    hooks default to empty tuples so new Domains do not need to implement every
    internal integration point up front.
    """

    @property
    def manifest(self) -> DomainManifest:
        raise NotImplementedError("DomainRuntime.manifest must be implemented")

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        raise NotImplementedError("DomainRuntime.capabilities must be implemented")

    def tools(self) -> tuple[Tool, ...]:
        raise NotImplementedError("DomainRuntime.tools must be implemented")

    def policies(self) -> tuple[Policy, ...]:
        return ()

    def evaluators(self) -> tuple[Evaluator, ...]:
        raise NotImplementedError("DomainRuntime.evaluators must be implemented")

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return ()

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
        return ()

    def world_updaters(self) -> tuple[WorldUpdater, ...]:
        return ()

    def task_expanders(self) -> tuple[TaskExpander, ...]:
        return ()

    def recovery_rules(self) -> tuple[RecoveryRule, ...]:
        return ()

    def action_argument_providers(self) -> tuple[ActionArgumentProvider, ...]:
        return ()

    def memories(self) -> tuple[MemoryRecord, ...]:
        return ()


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
    action_argument_providers: tuple[ActionArgumentProvider, ...]
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

    def action_argument_providers(self) -> tuple[ActionArgumentProvider, ...]:
        return tuple(item for domain in self.domains for item in domain.action_argument_providers)

    def action_argument_providers_for(
        self,
        identity: DomainIdentity,
    ) -> tuple[ActionArgumentProvider, ...]:
        domain = self.domain_for(identity)
        return () if domain is None else domain.action_argument_providers

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
        duplicates = duplicate_values(
            f"{identity.name}@{identity.version}" for identity in self.identities
        )
        if duplicates:
            raise DomainValidationError(f"duplicate domain identities: {', '.join(duplicates)}")

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


class _DomainManifestMetadataPayload(ConfigPayload):
    name: PydanticNonEmptyString
    version: PydanticNonEmptyString
    description: str = ""


class _DomainManifestSpecPayload(ConfigPayload):
    ontology: list[PydanticNonEmptyString] = Field(default_factory=list)
    capabilities: list[PydanticNonEmptyString]
    evaluators: list[PydanticNonEmptyString]


class _DomainManifestPayload(ConfigPayload):
    api_version: PydanticNonEmptyString = Field(alias="apiVersion")
    kind: PydanticNonEmptyString
    metadata: _DomainManifestMetadataPayload
    spec: _DomainManifestSpecPayload


@dataclass(frozen=True, slots=True)
class DomainRuntimeSpec:
    """Declarative Domain SDK input for assembling a DomainRuntime.

    This gives package authors one small interface for the common case: declare
    the manifest identity and the runtime extension objects once, then let the
    SDK derive the manifest's capability/evaluator references from those
    concrete declarations.
    """

    name: str
    version: str
    description: str
    capabilities: tuple[CapabilityDefinition, ...]
    tools: tuple[Tool, ...]
    evaluators: tuple[Evaluator, ...]
    api_version: str = "agent.nantian.dev/v1alpha1"
    kind: str = "Domain"
    ontology: tuple[str, ...] = ()
    policies: tuple[Policy, ...] = ()
    context_providers: tuple[DomainContextProvider, ...] = ()
    evidence_extractors: tuple[EvidenceExtractor, ...] = ()
    world_updaters: tuple[WorldUpdater, ...] = ()
    task_expanders: tuple[TaskExpander, ...] = ()
    recovery_rules: tuple[RecoveryRule, ...] = ()
    action_argument_providers: tuple[ActionArgumentProvider, ...] = ()
    memories: tuple[MemoryRecord, ...] = ()

    def __post_init__(self) -> None:
        _require_domain_spec_value(self.name, "name")
        _require_domain_spec_value(self.version, "version")
        _require_domain_spec_value(self.description, "description")
        _require_domain_spec_value(self.api_version, "api_version")
        _require_domain_spec_value(self.kind, "kind")
        _validate_domain_spec_names("ontology", self.ontology)
        _require_domain_spec_items("capabilities", self.capabilities)
        _require_domain_spec_items("tools", self.tools)
        _require_domain_spec_items("evaluators", self.evaluators)
        _validate_unique_domain_spec_names(
            "capabilities",
            tuple(capability.name for capability in self.capabilities),
        )
        _validate_unique_domain_spec_names(
            "tools",
            tuple(tool.definition.name for tool in self.tools),
        )
        _validate_unique_domain_spec_names(
            "evaluators",
            tuple(evaluator.name for evaluator in self.evaluators),
        )
        _validate_domain_spec_names("policies", tuple(policy.name for policy in self.policies))
        _validate_domain_spec_names(
            "context_providers",
            tuple(provider.name for provider in self.context_providers),
        )
        _validate_domain_spec_names(
            "evidence_extractors",
            tuple(extractor.name for extractor in self.evidence_extractors),
        )
        _validate_domain_spec_names(
            "world_updaters",
            tuple(updater.name for updater in self.world_updaters),
        )
        _validate_domain_spec_names(
            "task_expanders",
            tuple(expander.name for expander in self.task_expanders),
        )
        _validate_domain_spec_names(
            "recovery_rules",
            tuple(rule.name for rule in self.recovery_rules),
        )
        _validate_domain_spec_names(
            "action_argument_providers",
            tuple(provider.name for provider in self.action_argument_providers),
        )
        _validate_domain_spec_names(
            "memories",
            tuple(memory.subject for memory in self.memories),
        )

    @property
    def identity(self) -> DomainIdentity:
        return DomainIdentity(self.name, self.version)

    @property
    def capability_names(self) -> tuple[str, ...]:
        return tuple(capability.name for capability in self.capabilities)

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(tool.definition.name for tool in self.tools)

    @property
    def evaluator_names(self) -> tuple[str, ...]:
        return tuple(evaluator.name for evaluator in self.evaluators)

    @property
    def manifest(self) -> DomainManifest:
        return DomainManifest(
            self.api_version,
            self.kind,
            DomainMetadata(self.name, self.version, self.description),
            self.ontology,
            self.capability_names,
            self.evaluator_names,
        )


class DeclarativeDomainRuntime(BaseDomainRuntime):
    """DomainRuntime adapter backed by a DomainRuntimeSpec."""

    def __init__(self, spec: DomainRuntimeSpec) -> None:
        self._spec = spec

    @property
    def spec(self) -> DomainRuntimeSpec:
        return self._spec

    @property
    def manifest(self) -> DomainManifest:
        return self._spec.manifest

    def capabilities(self) -> tuple[CapabilityDefinition, ...]:
        return self._spec.capabilities

    def tools(self) -> tuple[Tool, ...]:
        return self._spec.tools

    def policies(self) -> tuple[Policy, ...]:
        return self._spec.policies

    def evaluators(self) -> tuple[Evaluator, ...]:
        return self._spec.evaluators

    def context_providers(self) -> tuple[DomainContextProvider, ...]:
        return self._spec.context_providers

    def evidence_extractors(self) -> tuple[EvidenceExtractor, ...]:
        return self._spec.evidence_extractors

    def world_updaters(self) -> tuple[WorldUpdater, ...]:
        return self._spec.world_updaters

    def task_expanders(self) -> tuple[TaskExpander, ...]:
        return self._spec.task_expanders

    def recovery_rules(self) -> tuple[RecoveryRule, ...]:
        return self._spec.recovery_rules

    def action_argument_providers(self) -> tuple[ActionArgumentProvider, ...]:
        return self._spec.action_argument_providers

    def memories(self) -> tuple[MemoryRecord, ...]:
        return self._spec.memories


def build_domain_runtime(spec: DomainRuntimeSpec) -> DeclarativeDomainRuntime:
    return DeclarativeDomainRuntime(spec)


def _require_domain_spec_value(value: str, field_name: str) -> None:
    try:
        parse_non_empty_string(value, f"domain runtime spec {field_name}")
    except ValueError as exc:
        raise DomainValidationError(str(exc)) from exc


def _require_domain_spec_items(label: str, values: tuple[object, ...]) -> None:
    if not values:
        raise DomainValidationError(
            f"domain runtime spec requires at least one {label} declaration"
        )


def _validate_domain_spec_names(label: str, names: tuple[str, ...]) -> None:
    try:
        parse_unique_non_empty_string_sequence(
            names,
            label,
            empty_template=f"domain runtime spec {label} must not include empty names",
            item_type_template=f"domain runtime spec {label} must not include empty names",
            duplicate_template=f"domain runtime spec contains duplicate {label}: {{duplicates}}",
        )
    except ValueError as exc:
        raise DomainValidationError(str(exc)) from exc


def _validate_unique_domain_spec_names(label: str, names: tuple[str, ...]) -> None:
    duplicates = duplicate_values(names)
    if duplicates:
        raise DomainValidationError(
            f"domain runtime spec contains duplicate {label}: " + ", ".join(duplicates)
        )


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
        action_argument_providers = _action_argument_providers(domain)
        memories = domain.memories()
        self._validate(
            manifest,
            capabilities,
            tools,
            evaluators,
            expanders,
            recovery_rules,
            action_argument_providers,
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
            action_argument_providers,
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
        action_argument_providers: tuple[ActionArgumentProvider, ...],
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
        for provider in action_argument_providers:
            unknown = set(provider.capability_names) - capability_names
            if unknown:
                names = ", ".join(sorted(unknown))
                raise DomainValidationError(
                    f"action argument provider {provider.name} references unknown "
                    f"capabilities: {names}"
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
        payload = _domain_manifest_payload(path)
        try:
            return DomainManifest(
                api_version=payload.api_version,
                kind=payload.kind,
                metadata=DomainMetadata(
                    name=payload.metadata.name,
                    version=payload.metadata.version,
                    description=payload.metadata.description,
                ),
                ontology=tuple(payload.spec.ontology),
                capability_names=tuple(payload.spec.capabilities),
                evaluator_names=tuple(payload.spec.evaluators),
            )
        except ValueError as exc:
            raise DomainValidationError(f"invalid domain manifest JSON: {exc}") from exc


def _domain_manifest_payload(path: Path) -> _DomainManifestPayload:
    try:
        values = parse_json_object(read_json_file(path), "domain manifest")
        return parse_payload(
            _DomainManifestPayload,
            values,
            missing_template="{path} is required",
        )
    except ValueError as exc:
        raise DomainValidationError(f"invalid domain manifest JSON: {exc}") from exc


def _action_argument_providers(domain: DomainRuntime) -> tuple[ActionArgumentProvider, ...]:
    method = getattr(domain, "action_argument_providers", None)
    if method is None:
        return ()
    if not callable(method):
        raise DomainValidationError("domain action_argument_providers must be callable")
    return tuple(cast(tuple[ActionArgumentProvider, ...], method()))
