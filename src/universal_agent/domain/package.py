from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from universal_agent.core import DomainIdentity, JsonMapping, immutable_json

DOMAIN_PACKAGE_MANIFEST = "manifest.json"


class DomainPackageValidationError(ValueError):
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


def load_domain_package(path: Path) -> DomainPackage:
    manifest_path = _manifest_path(path)
    payload = _load_json_object(manifest_path)
    manifest = decode_domain_package_manifest(payload)
    return DomainPackage(
        manifest=manifest,
        root_path=manifest_path.parent,
        manifest_path=manifest_path,
    )


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


def _format_identity(identity: DomainIdentity) -> str:
    return f"{identity.name}@{identity.version}"
