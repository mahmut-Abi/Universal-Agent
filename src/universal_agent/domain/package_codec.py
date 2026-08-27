from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import Field

from universal_agent.core import DomainIdentity, JsonMapping, immutable_json
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    json_mapping,
    parse_payload,
)
from universal_agent.domain.package_models import (
    DOMAIN_PACKAGE_MANIFEST,
    DomainPackage,
    DomainPackageCompatibility,
    DomainPackageManifest,
    DomainPackageNotFoundError,
    DomainPackageScaffoldSpec,
    DomainPackageValidationError,
    _default_entrypoint,
    _require_non_empty,
)
from universal_agent.domain.runtime import DomainRuntimeSpec


class _DomainPackageMetadataPayload(ConfigPayload):
    name: str
    version: str
    description: str
    author: str | None = None
    tags: list[str] = Field(default_factory=list)


class _DomainPackageCompatibilityPayload(ConfigPayload):
    runtime_api: str | None = None
    domain_api: str | None = None


class _DomainPackageDependencyPayload(ConfigPayload):
    name: str
    version: str


class _DomainPackageManifestPayload(ConfigPayload):
    api_version_camel: str | None = Field(default=None, alias="apiVersion")
    api_version: str | None = None
    kind: str
    metadata: dict[str, PydanticJsonValue]
    entrypoint: str | None = None
    ontology: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    policies: list[str] = Field(default_factory=list)
    procedures: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    evaluators: list[str] = Field(default_factory=list)
    context_providers: list[str] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    dependencies: list[_DomainPackageDependencyPayload] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    compatibility: dict[str, PydanticJsonValue] | None = None
    security: dict[str, PydanticJsonValue] | None = None


def load_domain_package(path: Path) -> DomainPackage:
    manifest_path = _manifest_path(path)
    payload = _load_json_object(manifest_path)
    manifest = decode_domain_package_manifest(payload)
    return DomainPackage(
        manifest=manifest,
        root_path=manifest_path.parent,
        manifest_path=manifest_path,
    )


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
    runtime_stub: bool = False,
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
        runtime_stub=runtime_stub,
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


def decode_domain_package_manifest(payload: JsonMapping) -> DomainPackageManifest:
    manifest_payload = _parse_domain_payload(_DomainPackageManifestPayload, payload)
    metadata_payload = _parse_domain_payload(
        _DomainPackageMetadataPayload,
        json_mapping(manifest_payload.metadata),
    )
    compatibility_payload = (
        None
        if manifest_payload.compatibility is None
        else _parse_domain_payload(
            _DomainPackageCompatibilityPayload,
            json_mapping(manifest_payload.compatibility),
        )
    )
    return DomainPackageManifest(
        api_version=_api_version(manifest_payload),
        kind=manifest_payload.kind,
        name=metadata_payload.name,
        version=metadata_payload.version,
        description=metadata_payload.description,
        author=metadata_payload.author,
        entrypoint=manifest_payload.entrypoint,
        ontology=_string_tuple(manifest_payload.ontology, "ontology"),
        capabilities=_string_tuple(manifest_payload.capabilities, "capabilities"),
        tools=_string_tuple(manifest_payload.tools, "tools"),
        policies=_string_tuple(manifest_payload.policies, "policies"),
        procedures=_string_tuple(manifest_payload.procedures, "procedures"),
        knowledge=_string_tuple(manifest_payload.knowledge, "knowledge"),
        evaluators=_string_tuple(manifest_payload.evaluators, "evaluators"),
        context_providers=_string_tuple(
            manifest_payload.context_providers,
            "context_providers",
        ),
        prompts=_string_tuple(manifest_payload.prompts, "prompts"),
        resources=_string_tuple(manifest_payload.resources, "resources"),
        dependencies=tuple(
            DomainIdentity(dependency.name, dependency.version)
            for dependency in manifest_payload.dependencies
        ),
        required_tools=_string_tuple(manifest_payload.required_tools, "required_tools"),
        compatibility=DomainPackageCompatibility(
            runtime_api=(
                None if compatibility_payload is None else compatibility_payload.runtime_api
            ),
            domain_api=None if compatibility_payload is None else compatibility_payload.domain_api,
        ),
        security=(
            immutable_json()
            if manifest_payload.security is None
            else immutable_json(json_mapping(manifest_payload.security))
        ),
        tags=_string_tuple(metadata_payload.tags, "tags"),
        metadata=immutable_json(json_mapping(manifest_payload.metadata)),
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


def _parse_domain_payload[T: ConfigPayload](
    model_type: type[T],
    payload: JsonMapping,
) -> T:
    try:
        return parse_payload(model_type, payload)
    except ValueError as exc:
        raise DomainPackageValidationError(str(exc)) from exc


def _api_version(payload: _DomainPackageManifestPayload) -> str:
    camel = payload.api_version_camel
    snake = payload.api_version
    if camel is not None and snake is not None and camel != snake:
        raise DomainPackageValidationError("apiVersion and api_version must match")
    value = camel if camel is not None else snake
    if value is None:
        raise DomainPackageValidationError("apiVersion must be a string")
    _require_non_empty(value, "apiVersion")
    return value


def _string_tuple(value: Sequence[str], key: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise DomainPackageValidationError(f"{key} must be a list of strings")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise DomainPackageValidationError(f"{key}[{index}] must be a non-empty string")
        items.append(item)
    return tuple(items)
