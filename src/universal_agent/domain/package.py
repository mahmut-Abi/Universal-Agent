from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from universal_agent.core import DomainIdentity, JsonMapping, immutable_json
from universal_agent.domain.runtime import (
    ActiveDomain,
    DomainLoader,
    DomainRuntime,
    DomainRuntimeSpec,
)

DOMAIN_PACKAGE_MANIFEST = "manifest.json"
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
        if self.domain_api is not None:
            _require_non_empty(self.domain_api, "compatibility.domain_api")


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


class DomainPackageRegistry:
    """P7 metadata registry for independently packaged Domain runtimes.

    The registry deliberately stores package metadata only. It does not import
    domain entrypoints or mutate Kernel runtime state; actual DomainRuntime
    activation remains owned by DomainManager/DomainLoader.
    """

    def __init__(self, packages: tuple[DomainPackage, ...] = ()) -> None:
        self._packages: dict[DomainIdentity, DomainPackage] = {}
        self._order: list[DomainIdentity] = []
        for package in packages:
            self.register(package)

    def register(self, package: DomainPackage) -> DomainPackage:
        identity = package.identity
        if identity in self._packages:
            raise DomainPackageValidationError(
                f"domain package already registered: {_format_identity(identity)}"
            )
        self._packages[identity] = package
        self._order.append(identity)
        return package

    def discover(self, root: Path) -> tuple[DomainPackage, ...]:
        packages = tuple(load_domain_package(path) for path in _manifest_paths(root))
        for package in packages:
            self.register(package)
        return packages

    def install(self, path: Path) -> DomainPackage:
        """Validate and register one package without activating its Domain code."""

        return self.register(load_domain_package(path))

    def list(self, *, tag: str | None = None) -> tuple[DomainPackage, ...]:
        packages = tuple(self._packages[identity] for identity in self._order)
        if tag is None:
            return packages
        return tuple(package for package in packages if tag in package.manifest.tags)

    def identities(self) -> tuple[DomainIdentity, ...]:
        return tuple(package.identity for package in self.list())

    def get(self, identity: DomainIdentity) -> DomainPackage:
        try:
            return self._packages[identity]
        except KeyError as exc:
            raise DomainPackageNotFoundError(
                f"domain package not registered: {_format_identity(identity)}"
            ) from exc

    def get_by_name(self, name: str) -> DomainPackage:
        matches = tuple(package for package in self.list() if package.identity.name == name)
        if not matches:
            raise DomainPackageNotFoundError(f"domain package not registered: {name}")
        if len(matches) > 1:
            versions = ", ".join(sorted(package.identity.version for package in matches))
            raise AmbiguousDomainPackageError(
                f"domain package {name} has multiple registered versions: {versions}"
            )
        return matches[0]

    def verify(self, *, verify_paths: bool = False) -> DomainPackageVerificationReport:
        return verify_domain_package_registry(self, verify_paths=verify_paths)


def load_domain_package(path: Path) -> DomainPackage:
    manifest_path = _manifest_path(path)
    payload = _load_json_object(manifest_path)
    manifest = decode_domain_package_manifest(payload)
    return DomainPackage(
        manifest=manifest,
        root_path=manifest_path.parent,
        manifest_path=manifest_path,
    )


def load_domain_package_runtime(
    package_or_path: DomainPackage | Path,
    *,
    loader: DomainLoader | None = None,
    verify_paths: bool = True,
) -> DomainPackageRuntimeActivation:
    """Load and validate DomainRuntime code from an explicit package entrypoint.

    This is intentionally separate from registry install/discovery. Installing a
    package records metadata; this function is the SDK seam that imports local
    package code, validates it through DomainLoader, and checks that the loaded
    Domain identity matches the package manifest identity.
    """

    package = (
        package_or_path
        if isinstance(package_or_path, DomainPackage)
        else load_domain_package(package_or_path)
    )
    if verify_paths:
        _raise_for_failed_verification(verify_domain_package(package))
    entrypoint = package.manifest.entrypoint
    if entrypoint is None:
        raise DomainPackageRuntimeLoadError(
            f"domain package {_format_identity(package.identity)} has no entrypoint"
        )

    runtime = _load_domain_runtime_entrypoint(package.root_path, entrypoint)
    domain_loader = loader or DomainLoader()
    active = domain_loader.load(runtime)
    if active.identity != package.identity:
        raise DomainPackageRuntimeLoadError(
            "domain package entrypoint identity mismatch: "
            f"package {_format_identity(package.identity)}, "
            f"runtime {_format_identity(active.identity)}"
        )
    _validate_package_runtime_metadata(package, active)
    return DomainPackageRuntimeActivation(package, runtime, active)


def build_domain_package_manifest(spec: DomainPackageScaffoldSpec) -> DomainPackageManifest:
    metadata = dict(spec.metadata)
    metadata.update(
        {
            "name": spec.name,
            "version": spec.version,
            "description": spec.description,
        }
    )
    if spec.author is not None:
        metadata["author"] = spec.author
    if spec.tags:
        metadata["tags"] = list(spec.tags)
    return DomainPackageManifest(
        api_version=spec.api_version,
        kind="DomainPackage",
        name=spec.name,
        version=spec.version,
        description=spec.description,
        author=spec.author,
        entrypoint=spec.entrypoint or _default_entrypoint(spec.name),
        ontology=spec.ontology,
        capabilities=spec.capabilities,
        tools=spec.tools,
        policies=spec.policies,
        procedures=spec.procedures,
        knowledge=spec.knowledge,
        evaluators=spec.evaluators,
        context_providers=spec.context_providers,
        prompts=spec.prompts,
        resources=spec.resources,
        dependencies=spec.dependencies,
        required_tools=spec.required_tools,
        compatibility=spec.compatibility,
        security=immutable_json(spec.security),
        tags=spec.tags,
        metadata=immutable_json(metadata),
    )


def domain_package_scaffold_spec_from_runtime_spec(
    spec: DomainRuntimeSpec,
    *,
    author: str | None = None,
    entrypoint: str | None = None,
    procedures: tuple[str, ...] = (),
    knowledge: tuple[str, ...] = (),
    prompts: tuple[str, ...] = (),
    resources: tuple[str, ...] = (),
    dependencies: tuple[DomainIdentity, ...] = (),
    required_tools: tuple[str, ...] = (),
    compatibility: DomainPackageCompatibility | None = None,
    security: JsonMapping | None = None,
    tags: tuple[str, ...] = (),
    metadata: JsonMapping | None = None,
) -> DomainPackageScaffoldSpec:
    """Project a declarative Domain runtime spec into package scaffold metadata.

    The package manifest remains metadata-only and does not import or activate
    Domain code. This helper only keeps the manifest's declared capability,
    tool, policy, evaluator and context-provider names aligned with the runtime
    objects already declared by Domain authors.
    """

    return DomainPackageScaffoldSpec(
        name=spec.name,
        version=spec.version,
        description=spec.description,
        api_version=spec.api_version,
        author=author,
        entrypoint=entrypoint,
        ontology=spec.ontology,
        capabilities=spec.capability_names,
        tools=spec.tool_names,
        policies=tuple(policy.name for policy in spec.policies),
        procedures=procedures,
        knowledge=knowledge,
        evaluators=spec.evaluator_names,
        context_providers=tuple(provider.name for provider in spec.context_providers),
        prompts=prompts,
        resources=resources,
        dependencies=dependencies,
        required_tools=required_tools,
        compatibility=compatibility or DomainPackageCompatibility(),
        security=immutable_json(security),
        tags=tags,
        metadata=immutable_json(metadata),
    )


def encode_domain_package_manifest(manifest: DomainPackageManifest) -> dict[str, Any]:
    metadata = dict(manifest.metadata)
    metadata.update(
        {
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
        }
    )
    if manifest.author is not None:
        metadata["author"] = manifest.author
    if manifest.tags:
        metadata["tags"] = list(manifest.tags)
    payload: dict[str, Any] = {
        "apiVersion": manifest.api_version,
        "kind": manifest.kind,
        "metadata": metadata,
        "entrypoint": manifest.entrypoint,
        "ontology": list(manifest.ontology),
        "capabilities": list(manifest.capabilities),
        "tools": list(manifest.tools),
        "policies": list(manifest.policies),
        "procedures": list(manifest.procedures),
        "knowledge": list(manifest.knowledge),
        "evaluators": list(manifest.evaluators),
        "context_providers": list(manifest.context_providers),
        "prompts": list(manifest.prompts),
        "resources": list(manifest.resources),
        "dependencies": [
            {"name": dependency.name, "version": dependency.version}
            for dependency in manifest.dependencies
        ],
        "required_tools": list(manifest.required_tools),
    }
    compatibility: dict[str, str] = {}
    if manifest.compatibility.runtime_api is not None:
        compatibility["runtime_api"] = manifest.compatibility.runtime_api
    if manifest.compatibility.domain_api is not None:
        compatibility["domain_api"] = manifest.compatibility.domain_api
    if compatibility:
        payload["compatibility"] = compatibility
    if manifest.security:
        payload["security"] = dict(manifest.security)
    return payload


def scaffold_domain_package(
    root: Path,
    spec: DomainPackageScaffoldSpec,
    *,
    overwrite: bool = False,
) -> DomainPackageScaffoldResult:
    """Create a package skeleton that can be validated by DomainPackageRegistry."""

    manifest = build_domain_package_manifest(spec)
    manifest_path = root / DOMAIN_PACKAGE_MANIFEST
    if manifest_path.exists() and not overwrite:
        raise DomainPackageValidationError(
            f"domain package manifest already exists: {manifest_path}"
        )

    created_paths: list[Path] = []
    if not root.exists():
        root.mkdir(parents=True)
        created_paths.append(root)
    elif not root.is_dir():
        raise DomainPackageValidationError(f"domain package root must be a directory: {root}")

    for directory_name in DOMAIN_PACKAGE_DIRECTORIES:
        directory = root / directory_name
        if not directory.exists():
            directory.mkdir()
            created_paths.append(directory)
        elif not directory.is_dir():
            raise DomainPackageValidationError(
                f"domain package scaffold path must be a directory: {directory}"
            )

    for resource in spec.resources:
        _validate_package_resource(resource)
        resource_path = root / resource
        if resource_path.suffix:
            directory = resource_path.parent
        else:
            directory = resource_path
        if not directory.exists():
            directory.mkdir(parents=True)
            created_paths.append(directory)
        elif not directory.is_dir():
            raise DomainPackageValidationError(
                f"domain package scaffold resource parent must be a directory: {directory}"
            )
        if resource_path.suffix and not resource_path.exists():
            resource_path.touch()
            created_paths.append(resource_path)

    overwritten = manifest_path.exists()
    _write_json_manifest(manifest_path, encode_domain_package_manifest(manifest))
    package = load_domain_package(root)
    return DomainPackageScaffoldResult(
        package=package,
        created_paths=tuple(created_paths),
        written_paths=(manifest_path,),
        overwritten=overwritten,
    )


def verify_domain_package(package: DomainPackage) -> DomainPackageVerificationReport:
    return DomainPackageVerificationReport(
        (
            _package_root_exists(package),
            _package_manifest_exists(package),
            _package_manifest_matches_identity(package),
            _package_resources_exist(package),
        )
    )


def verify_domain_package_registry(
    registry: DomainPackageRegistry,
    *,
    verify_paths: bool = False,
) -> DomainPackageVerificationReport:
    packages = registry.list()
    registered_domains = frozenset(package.identity for package in packages)
    checks = [
        _package_dependencies_registered(packages, registered_domains),
        _package_dependencies_acyclic(packages),
    ]
    if verify_paths:
        checks.extend(_package_registry_local_checks(packages))
    return DomainPackageVerificationReport(tuple(checks))


def decode_domain_package_manifest(payload: JsonMapping) -> DomainPackageManifest:
    metadata = _mapping(payload, "metadata")
    compatibility = _optional_mapping(payload, "compatibility")
    return DomainPackageManifest(
        api_version=_api_version(payload),
        kind=_string(payload, "kind"),
        name=_string(metadata, "name", field_name="metadata.name"),
        version=_string(metadata, "version", field_name="metadata.version"),
        description=_string(metadata, "description", field_name="metadata.description"),
        author=_optional_string(metadata, "author", field_name="metadata.author"),
        entrypoint=_optional_string(payload, "entrypoint"),
        ontology=_string_tuple(payload, "ontology"),
        capabilities=_string_tuple(payload, "capabilities"),
        tools=_string_tuple(payload, "tools"),
        policies=_string_tuple(payload, "policies"),
        procedures=_string_tuple(payload, "procedures"),
        knowledge=_string_tuple(payload, "knowledge"),
        evaluators=_string_tuple(payload, "evaluators"),
        context_providers=_string_tuple(payload, "context_providers"),
        prompts=_string_tuple(payload, "prompts"),
        resources=_string_tuple(payload, "resources"),
        dependencies=_identity_tuple(payload, "dependencies"),
        required_tools=_string_tuple(payload, "required_tools"),
        compatibility=DomainPackageCompatibility(
            runtime_api=(
                None
                if compatibility is None
                else _optional_string(
                    compatibility, "runtime_api", field_name="compatibility.runtime_api"
                )
            ),
            domain_api=(
                None
                if compatibility is None
                else _optional_string(
                    compatibility, "domain_api", field_name="compatibility.domain_api"
                )
            ),
        ),
        security=_optional_mapping(payload, "security") or immutable_json(),
        tags=_string_tuple(metadata, "tags"),
        metadata=immutable_json(metadata),
    )


def _manifest_paths(root: Path) -> tuple[Path, ...]:
    if root.is_file():
        return (_manifest_path(root),)
    if not root.exists():
        raise DomainPackageNotFoundError(f"domain package root not found: {root}")
    return tuple(sorted(root.rglob(DOMAIN_PACKAGE_MANIFEST)))


def _manifest_path(path: Path) -> Path:
    if path.is_dir():
        return path / DOMAIN_PACKAGE_MANIFEST
    return path


def _load_json_object(path: Path) -> JsonMapping:
    if not path.exists():
        raise DomainPackageNotFoundError(f"domain package manifest not found: {path}")
    try:
        loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DomainPackageValidationError(f"invalid domain package manifest JSON: {path}") from exc
    if not isinstance(loaded, dict):
        raise DomainPackageValidationError("domain package manifest must be a JSON object")
    return immutable_json(loaded)


def _load_domain_runtime_entrypoint(root_path: Path, entrypoint: str) -> DomainRuntime:
    module_name, attribute_path = _parse_entrypoint(entrypoint)
    with _package_import_path(root_path):
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise DomainPackageRuntimeLoadError(
                f"domain package entrypoint module could not be imported: {module_name}"
            ) from exc
        target = _resolve_entrypoint_attribute(module, attribute_path)
        return _coerce_domain_runtime(target, entrypoint)


def _parse_entrypoint(entrypoint: str) -> tuple[str, tuple[str, ...]]:
    if ":" not in entrypoint:
        raise DomainPackageRuntimeLoadError(
            "domain package entrypoint must use 'module:attribute' format"
        )
    module_name, raw_attribute = entrypoint.split(":", 1)
    if not module_name.strip() or not raw_attribute.strip():
        raise DomainPackageRuntimeLoadError(
            "domain package entrypoint must include module and attribute"
        )
    attribute_path = tuple(part for part in raw_attribute.split(".") if part)
    if "." in raw_attribute and len(attribute_path) != len(raw_attribute.split(".")):
        raise DomainPackageRuntimeLoadError("domain package entrypoint attribute is invalid")
    return module_name, attribute_path


@contextmanager
def _package_import_path(root_path: Path) -> Iterator[None]:
    root = str(root_path)
    inserted = False
    if root not in sys.path:
        sys.path.insert(0, root)
        inserted = True
    try:
        yield
    finally:
        if inserted:
            try:
                sys.path.remove(root)
            except ValueError:  # pragma: no cover - defensive against external sys.path mutation
                pass


def _resolve_entrypoint_attribute(module: ModuleType, attribute_path: tuple[str, ...]) -> Any:
    target: Any = module
    traversed: list[str] = [module.__name__]
    for attribute in attribute_path:
        traversed.append(attribute)
        try:
            target = getattr(target, attribute)
        except AttributeError as exc:
            raise DomainPackageRuntimeLoadError(
                "domain package entrypoint attribute not found: " + ".".join(traversed)
            ) from exc
    return target


def _coerce_domain_runtime(target: Any, entrypoint: str) -> DomainRuntime:
    if _looks_like_domain_runtime(target):
        return cast(DomainRuntime, target)
    if isinstance(target, type):
        try:
            instance = target()
        except TypeError as exc:
            raise DomainPackageRuntimeLoadError(
                f"domain package entrypoint class requires unsupported arguments: {entrypoint}"
            ) from exc
        if _looks_like_domain_runtime(instance):
            return cast(DomainRuntime, instance)
    if callable(target):
        try:
            result = target()
        except TypeError as exc:
            raise DomainPackageRuntimeLoadError(
                f"domain package entrypoint callable requires unsupported arguments: {entrypoint}"
            ) from exc
        if _looks_like_domain_runtime(result):
            return cast(DomainRuntime, result)
        if isinstance(result, type):
            return _coerce_domain_runtime(result, entrypoint)
    raise DomainPackageRuntimeLoadError(
        f"domain package entrypoint did not produce a DomainRuntime: {entrypoint}"
    )


def _looks_like_domain_runtime(candidate: object) -> bool:
    return (
        hasattr(candidate, "manifest")
        and callable(getattr(candidate, "capabilities", None))
        and callable(getattr(candidate, "tools", None))
        and callable(getattr(candidate, "evaluators", None))
    )


def _raise_for_failed_verification(report: DomainPackageVerificationReport) -> None:
    if report.passed:
        return
    details = "; ".join(f"{check.name}: {check.message}" for check in report.failed_checks)
    raise DomainPackageRuntimeLoadError(f"domain package verification failed: {details}")


def _validate_package_runtime_metadata(package: DomainPackage, active: ActiveDomain) -> None:
    _validate_declared_runtime_names(
        "capabilities",
        package.manifest.capabilities,
        tuple(capability.name for capability in active.capabilities),
    )
    _validate_declared_runtime_names(
        "tools",
        package.manifest.tools,
        tuple(tool.definition.name for tool in active.tools),
    )
    _validate_declared_runtime_names(
        "evaluators",
        package.manifest.evaluators,
        tuple(evaluator.name for evaluator in active.evaluators),
    )


def _validate_declared_runtime_names(
    field_name: str,
    declared: tuple[str, ...],
    actual: tuple[str, ...],
) -> None:
    if set(declared) == set(actual):
        return
    raise DomainPackageRuntimeLoadError(
        f"domain package entrypoint {field_name} mismatch: "
        f"manifest [{', '.join(sorted(declared))}], "
        f"runtime [{', '.join(sorted(actual))}]"
    )


def _package_root_exists(package: DomainPackage) -> DomainPackageCheck:
    if package.root_path.is_dir():
        return DomainPackageCheck(
            "package_root_exists",
            True,
            "domain package root exists",
        )
    return DomainPackageCheck(
        "package_root_exists",
        False,
        f"domain package root missing or not a directory: {package.root_path}",
    )


def _package_manifest_exists(package: DomainPackage) -> DomainPackageCheck:
    if package.manifest_path.is_file():
        return DomainPackageCheck(
            "package_manifest_exists",
            True,
            "domain package manifest exists",
        )
    return DomainPackageCheck(
        "package_manifest_exists",
        False,
        f"domain package manifest missing or not a file: {package.manifest_path}",
    )


def _package_manifest_matches_identity(package: DomainPackage) -> DomainPackageCheck:
    try:
        loaded = load_domain_package(package.manifest_path)
    except (DomainPackageNotFoundError, DomainPackageValidationError) as exc:
        return DomainPackageCheck(
            "package_manifest_matches_identity",
            False,
            f"domain package manifest could not be loaded: {exc}",
        )
    if loaded.identity == package.identity:
        return DomainPackageCheck(
            "package_manifest_matches_identity",
            True,
            "domain package manifest identity matches loaded package",
        )
    return DomainPackageCheck(
        "package_manifest_matches_identity",
        False,
        "domain package identity mismatch: "
        f"expected {_format_identity(package.identity)}, "
        f"loaded {_format_identity(loaded.identity)}",
    )


def _package_resources_exist(package: DomainPackage) -> DomainPackageCheck:
    invalid: list[str] = []
    missing: list[str] = []
    for resource in package.manifest.resources:
        try:
            resource_path = _package_resource_path(package.root_path, resource)
        except DomainPackageValidationError:
            invalid.append(resource)
            continue
        if not resource_path.exists():
            missing.append(resource)

    if invalid:
        return DomainPackageCheck(
            "package_resources_exist",
            False,
            "domain package resources must stay inside package root: " + ", ".join(invalid),
        )
    if missing:
        return DomainPackageCheck(
            "package_resources_exist",
            False,
            "domain package declares missing resources: " + ", ".join(missing),
        )
    return DomainPackageCheck(
        "package_resources_exist",
        True,
        "domain package declared resources exist",
    )


def _package_dependencies_registered(
    packages: tuple[DomainPackage, ...],
    registered_domains: frozenset[DomainIdentity],
) -> DomainPackageCheck:
    missing = tuple(
        f"{package.identity.name}:{dependency.name}@{dependency.version}"
        for package in packages
        for dependency in package.manifest.dependencies
        if dependency not in registered_domains
    )
    if not missing:
        return DomainPackageCheck(
            "package_dependencies_registered",
            True,
            "all Domain package dependencies are registered",
        )
    return DomainPackageCheck(
        "package_dependencies_registered",
        False,
        "Domain packages reference missing dependencies: " + ", ".join(missing),
    )


def _package_dependencies_acyclic(packages: tuple[DomainPackage, ...]) -> DomainPackageCheck:
    dependency_map = {package.identity: package.manifest.dependencies for package in packages}
    cycles = _dependency_cycles(dependency_map)
    if not cycles:
        return DomainPackageCheck(
            "package_dependencies_acyclic",
            True,
            "Domain package dependencies are acyclic",
        )
    return DomainPackageCheck(
        "package_dependencies_acyclic",
        False,
        "Domain package dependencies contain cycles: " + ", ".join(cycles),
    )


def _package_registry_local_checks(
    packages: tuple[DomainPackage, ...],
) -> tuple[DomainPackageCheck, ...]:
    checks: list[DomainPackageCheck] = []
    for package in packages:
        identity = _format_identity(package.identity)
        for check in verify_domain_package(package).checks:
            checks.append(
                DomainPackageCheck(
                    f"{check.name}:{identity}",
                    check.passed,
                    f"{identity}: {check.message}",
                )
            )
    return tuple(checks)


def _dependency_cycles(
    dependency_map: dict[DomainIdentity, tuple[DomainIdentity, ...]],
) -> tuple[str, ...]:
    visiting: set[DomainIdentity] = set()
    visited: set[DomainIdentity] = set()
    stack: list[DomainIdentity] = []
    cycles: set[str] = set()

    def visit(identity: DomainIdentity) -> None:
        if identity in visited:
            return
        if identity in visiting:
            cycle = [*stack[stack.index(identity) :], identity]
            cycles.add(" -> ".join(_format_identity(item) for item in cycle))
            return
        visiting.add(identity)
        stack.append(identity)
        for dependency in dependency_map.get(identity, ()):
            if dependency in dependency_map:
                visit(dependency)
        stack.pop()
        visiting.remove(identity)
        visited.add(identity)

    for identity in dependency_map:
        visit(identity)
    return tuple(sorted(cycles))


def _write_json_manifest(path: Path, payload: JsonMapping) -> None:
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(path)


def _mapping(payload: JsonMapping, key: str) -> JsonMapping:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise DomainPackageValidationError(f"{key} must be an object")
    return immutable_json(value)


def _optional_mapping(payload: JsonMapping, key: str) -> JsonMapping | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DomainPackageValidationError(f"{key} must be an object")
    return immutable_json(value)


def _api_version(payload: JsonMapping) -> str:
    camel = payload.get("apiVersion")
    snake = payload.get("api_version")
    if camel is not None and snake is not None and camel != snake:
        raise DomainPackageValidationError("apiVersion and api_version must match")
    value = camel if camel is not None else snake
    return _string_value(value, "apiVersion")


def _string(payload: JsonMapping, key: str, *, field_name: str | None = None) -> str:
    value = payload.get(key)
    return _string_value(value, field_name or key)


def _string_value(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise DomainPackageValidationError(f"{field_name} must be a string")
    _require_non_empty(value, field_name)
    return value


def _optional_string(
    payload: JsonMapping, key: str, *, field_name: str | None = None
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    return _string_value(value, field_name or key)


def _string_tuple(payload: JsonMapping, key: str) -> tuple[str, ...]:
    value = payload.get(key, ())
    if not isinstance(value, list | tuple):
        raise DomainPackageValidationError(f"{key} must be a list of strings")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise DomainPackageValidationError(f"{key}[{index}] must be a non-empty string")
        items.append(item)
    return tuple(items)


def _identity_tuple(payload: JsonMapping, key: str) -> tuple[DomainIdentity, ...]:
    value = payload.get(key, ())
    if not isinstance(value, list | tuple):
        raise DomainPackageValidationError(f"{key} must be a list of dependency objects")
    identities: list[DomainIdentity] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise DomainPackageValidationError(f"{key}[{index}] must be an object")
        dependency = immutable_json(item)
        identities.append(
            DomainIdentity(
                _string(dependency, "name", field_name=f"{key}[{index}].name"),
                _string(dependency, "version", field_name=f"{key}[{index}].version"),
            )
        )
    return tuple(identities)


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise DomainPackageValidationError(f"{field_name} must not be empty")


def _validate_strings(field_name: str, values: Sequence[str]) -> None:
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            raise DomainPackageValidationError(f"{field_name}[{index}] must be a non-empty string")


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
    try:
        resolved_resource.relative_to(resolved_root)
    except ValueError as exc:
        raise DomainPackageValidationError(
            f"domain package resource path must stay inside package root: {resource}"
        ) from exc
    return resolved_resource


def _default_entrypoint(name: str) -> str:
    module_name = name.replace("-", "_")
    return f"{module_name}.domain:build_domain"


def _format_identity(identity: DomainIdentity) -> str:
    return f"{identity.name}@{identity.version}"
