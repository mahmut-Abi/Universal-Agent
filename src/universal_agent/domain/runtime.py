from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from universal_agent.context import DomainContextProvider
from universal_agent.core import (
    CapabilityDefinition,
    DomainManifest,
    DomainMetadata,
    JsonValue,
)
from universal_agent.evaluation import Evaluator
from universal_agent.evidence import EvidenceExtractor
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
        self._validate(
            manifest,
            capabilities,
            tools,
            evaluators,
            expanders,
            recovery_rules,
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
        )

    def _validate(
        self,
        manifest: DomainManifest,
        capabilities: tuple[CapabilityDefinition, ...],
        tools: tuple[Tool, ...],
        evaluators: tuple[Evaluator, ...],
        expanders: tuple[TaskExpander, ...],
        recovery_rules: tuple[RecoveryRule, ...],
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
