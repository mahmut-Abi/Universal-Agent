from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from universal_agent.core import DomainIdentity, JsonMapping, immutable_json
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
