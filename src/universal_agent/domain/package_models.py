from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from universal_agent.core import DomainIdentity, JsonMapping, immutable_json
from universal_agent.core.config_validation import (
    parse_non_empty_string,
    parse_non_empty_string_sequence,
)
from universal_agent.domain.runtime import ActiveDomain, DomainRuntime

DOMAIN_PACKAGE_MANIFEST = "manifest.json"
DOMAIN_PACKAGE_MANIFESTS = (
    DOMAIN_PACKAGE_MANIFEST,
    "manifest.yaml",
    "manifest.yml",
)
DOMAIN_PACKAGE_DIRECTORIES = (
    "ontology",
    "capabilities",
    "tools",
    "policies",
    "procedures",
    "knowledge",
    "evaluators",
    "context_providers",
    "prompts",
    "resources",
    "tests",
)


class DomainPackageValidationError(ValueError):
    pass


class DomainPackageRuntimeLoadError(ValueError):
    pass


class DomainPackageNotFoundError(LookupError):
    pass


class AmbiguousDomainPackageError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DomainPackageCompatibility:
    runtime_api: str | None = None
    domain_api: str | None = None

    def __post_init__(self) -> None:
        if self.runtime_api is not None:
            _require_non_empty(self.runtime_api, "compatibility.runtime_api")
            _runtime_api_specifier(self.runtime_api, field_name="compatibility.runtime_api")
        if self.domain_api is not None:
            _require_non_empty(self.domain_api, "compatibility.domain_api")

    def supports_runtime_api(self, version: str) -> bool:
        """Return whether a runtime API version satisfies this package compatibility."""

        _require_non_empty(version, "runtime_api version")
        if self.runtime_api is None:
            return True
        try:
            runtime_version = Version(version)
        except InvalidVersion as exc:
            raise DomainPackageValidationError(
                f"runtime_api version must be PEP 440 compatible: {version}"
            ) from exc
        return runtime_version in _runtime_api_specifier(
            self.runtime_api,
            field_name="compatibility.runtime_api",
        )


@dataclass(frozen=True, slots=True)
class DomainPackageManifest:
    api_version: str
    kind: str
    name: str
    version: str
    description: str
    author: str | None = None
    entrypoint: str | None = None
    ontology: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    policies: tuple[str, ...] = ()
    procedures: tuple[str, ...] = ()
    knowledge: tuple[str, ...] = ()
    evaluators: tuple[str, ...] = ()
    context_providers: tuple[str, ...] = ()
    prompts: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    dependencies: tuple[DomainIdentity, ...] = ()
    required_tools: tuple[str, ...] = ()
    compatibility: DomainPackageCompatibility = field(default_factory=DomainPackageCompatibility)
    security: JsonMapping = field(default_factory=immutable_json)
    tags: tuple[str, ...] = ()
    metadata: JsonMapping = field(default_factory=immutable_json)

    def __post_init__(self) -> None:
        _require_non_empty(self.api_version, "api_version")
        _require_non_empty(self.kind, "kind")
        _require_non_empty(self.name, "metadata.name")
        _require_non_empty(self.version, "metadata.version")
        _require_non_empty(self.description, "metadata.description")
        if self.kind != "DomainPackage":
            raise DomainPackageValidationError("kind must be DomainPackage")
        if self.author is not None:
            _require_non_empty(self.author, "metadata.author")
        if self.entrypoint is not None:
            _require_non_empty(self.entrypoint, "entrypoint")
        _validate_strings("resources", self.resources)
        _validate_package_resources(self.resources)
        for index, dependency in enumerate(self.dependencies):
            _require_non_empty(dependency.name, f"dependencies[{index}].name")
            _require_non_empty(dependency.version, f"dependencies[{index}].version")

    @property
    def identity(self) -> DomainIdentity:
        return DomainIdentity(self.name, self.version)


@dataclass(frozen=True, slots=True)
class DomainPackage:
    manifest: DomainPackageManifest
    root_path: Path
    manifest_path: Path

    @property
    def identity(self) -> DomainIdentity:
        return self.manifest.identity


@dataclass(frozen=True, slots=True)
class DomainPackageCheck:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class DomainPackageVerificationReport:
    checks: tuple[DomainPackageCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed_checks(self) -> tuple[DomainPackageCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)


@dataclass(frozen=True, slots=True)
class DomainPackageScaffoldSpec:
    """SDK input for generating a package-shaped Domain Runtime directory."""

    name: str
    description: str
    version: str = "0.1.0"
    api_version: str = "agent.nantian.dev/v1alpha1"
    author: str | None = None
    entrypoint: str | None = None
    ontology: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    policies: tuple[str, ...] = ()
    procedures: tuple[str, ...] = ()
    knowledge: tuple[str, ...] = ()
    evaluators: tuple[str, ...] = ()
    context_providers: tuple[str, ...] = ()
    prompts: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    dependencies: tuple[DomainIdentity, ...] = ()
    required_tools: tuple[str, ...] = ()
    compatibility: DomainPackageCompatibility = field(default_factory=DomainPackageCompatibility)
    security: JsonMapping = field(default_factory=immutable_json)
    tags: tuple[str, ...] = ()
    metadata: JsonMapping = field(default_factory=immutable_json)
    runtime_stub: bool = False

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "name")
        _require_non_empty(self.description, "description")
        _require_non_empty(self.version, "version")
        _require_non_empty(self.api_version, "api_version")
        if self.author is not None:
            _require_non_empty(self.author, "author")
        if self.entrypoint is not None:
            _require_non_empty(self.entrypoint, "entrypoint")
        _validate_strings("ontology", self.ontology)
        _validate_strings("capabilities", self.capabilities)
        _validate_strings("tools", self.tools)
        _validate_strings("policies", self.policies)
        _validate_strings("procedures", self.procedures)
        _validate_strings("knowledge", self.knowledge)
        _validate_strings("evaluators", self.evaluators)
        _validate_strings("context_providers", self.context_providers)
        _validate_strings("prompts", self.prompts)
        _validate_strings("resources", self.resources)
        _validate_package_resources(self.resources)
        _validate_strings("required_tools", self.required_tools)
        _validate_strings("tags", self.tags)
        for index, dependency in enumerate(self.dependencies):
            _require_non_empty(dependency.name, f"dependencies[{index}].name")
            _require_non_empty(dependency.version, f"dependencies[{index}].version")


@dataclass(frozen=True, slots=True)
class DomainPackageScaffoldResult:
    package: DomainPackage
    created_paths: tuple[Path, ...]
    written_paths: tuple[Path, ...]
    runtime_stub_paths: tuple[Path, ...] = ()
    overwritten: bool = False


@dataclass(frozen=True, slots=True)
class DomainPackageRuntimeActivation:
    """Explicit SDK result for activating Domain code from a package entrypoint.

    Package registries intentionally keep code loading out of installation. This
    result is returned only by the explicit package runtime loader seam.
    """

    package: DomainPackage
    runtime: DomainRuntime
    active_domain: ActiveDomain


def _require_non_empty(value: str, field_name: str) -> None:
    try:
        parse_non_empty_string(value, field_name)
    except ValueError as exc:
        raise DomainPackageValidationError(str(exc)) from exc


def _runtime_api_specifier(value: str, *, field_name: str) -> SpecifierSet:
    try:
        return SpecifierSet(value)
    except InvalidSpecifier as exc:
        raise DomainPackageValidationError(
            f"{field_name} must be a valid version specifier: {value}"
        ) from exc


def _validate_strings(field_name: str, values: Sequence[str]) -> None:
    try:
        parse_non_empty_string_sequence(
            tuple(values),
            field_name,
            empty_template="{path} must be a non-empty string",
            item_type_template="{path} must be a non-empty string",
        )
    except ValueError as exc:
        raise DomainPackageValidationError(str(exc)) from exc


def _validate_package_resources(resources: Sequence[str]) -> None:
    for resource in resources:
        _validate_package_resource(resource)


def _validate_package_resource(resource: str) -> None:
    resource_path = Path(resource)
    if resource_path.is_absolute() or any(part == ".." for part in resource_path.parts):
        raise DomainPackageValidationError(
            f"domain package resource path must stay inside package root: {resource}"
        )


def _package_resource_path(root: Path, resource: str) -> Path:
    _validate_package_resource(resource)
    resolved_root = root.resolve()
    resolved_resource = (resolved_root / resource).resolve()
    if not resolved_resource.is_relative_to(resolved_root):
        raise DomainPackageValidationError(
            f"domain package resource path must stay inside package root: {resource}"
        )
    return resolved_resource


def _default_entrypoint(name: str) -> str:
    module_name = name.replace("-", "_")
    return f"{module_name}.domain:build_domain"


def _format_identity(identity: DomainIdentity) -> str:
    return f"{identity.name}@{identity.version}"
