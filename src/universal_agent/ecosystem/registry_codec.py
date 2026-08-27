from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from universal_agent.core import (
    DomainIdentity,
    JsonCodecError,
    JsonMapping,
    immutable_json,
    read_json_file,
    write_json,
)
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    json_mapping,
    parse_non_empty_string_sequence,
    parse_payload,
)
from universal_agent.domain import DomainPackageCompatibility, DomainPackageValidationError
from universal_agent.ecosystem.models import (
    EcosystemDomainPackageRef,
    EcosystemEvaluationDatasetRef,
    EcosystemEvaluationDatasetSuiteRef,
    EcosystemProfileRef,
    EcosystemRegistryManifest,
    EcosystemRegistryNotFoundError,
    EcosystemRegistryValidationError,
    EcosystemRegistryWriteResult,
)
from universal_agent.ecosystem.validation import _require_non_empty

if TYPE_CHECKING:
    from universal_agent.ecosystem.registry_index import EcosystemRegistryIndex


class _EcosystemRegistryMetadataPayload(ConfigPayload):
    name: str
    version: str
    description: str


class _EcosystemRegistryIdentityPayload(ConfigPayload):
    name: str
    version: str


class _EcosystemRegistryCompatibilityPayload(ConfigPayload):
    runtime_api: str | None = None
    domain_api: str | None = None


class _EcosystemRegistryDomainPackagePayload(ConfigPayload):
    name: str
    version: str
    description: str
    author: str | None = None
    entrypoint: str | None = None
    tags: list[str] = Field(default_factory=list)
    capability_names: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    dependencies: list[_EcosystemRegistryIdentityPayload] = Field(default_factory=list)
    compatibility: dict[str, PydanticJsonValue] | None = None
    security: dict[str, PydanticJsonValue] | None = None
    root_path: str | None = None
    manifest_path: str | None = None
    manifest_sha256: str | None = None


class _EcosystemRegistryDatasetSuitePayload(ConfigPayload):
    name: str
    path: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


class _EcosystemRegistryEvaluationDatasetPayload(ConfigPayload):
    name: str
    version: str
    description: str
    author: str | None = None
    tags: list[str] = Field(default_factory=list)
    domains: list[_EcosystemRegistryIdentityPayload] = Field(default_factory=list)
    suites: list[_EcosystemRegistryDatasetSuitePayload] = Field(default_factory=list)
    root_path: str | None = None
    manifest_path: str | None = None
    manifest_sha256: str | None = None


class _EcosystemRegistryProfilePayload(ConfigPayload):
    name: str
    version: str
    description: str
    domains: list[_EcosystemRegistryIdentityPayload] = Field(default_factory=list)
    path: str | None = None
    config_sha256: str | None = None


class _EcosystemRegistryManifestPayload(ConfigPayload):
    api_version_camel: str | None = Field(default=None, alias="apiVersion")
    api_version: str | None = None
    kind: str
    metadata: dict[str, PydanticJsonValue]
    domain_packages: list[_EcosystemRegistryDomainPackagePayload] = Field(default_factory=list)
    evaluation_datasets: list[_EcosystemRegistryEvaluationDatasetPayload] = Field(
        default_factory=list
    )
    profiles: list[_EcosystemRegistryProfilePayload] = Field(default_factory=list)


def encode_ecosystem_registry_manifest(manifest: EcosystemRegistryManifest) -> dict[str, Any]:
    metadata = dict(manifest.metadata)
    metadata.update(
        {
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
        }
    )
    return {
        "apiVersion": manifest.api_version,
        "kind": manifest.kind,
        "metadata": metadata,
        "summary": {
            "domain_package_count": manifest.summary.domain_package_count,
            "evaluation_dataset_count": manifest.summary.evaluation_dataset_count,
            "profile_count": manifest.summary.profile_count,
            "total_items": manifest.summary.total_items,
        },
        "domain_packages": [
            {
                "name": package.name,
                "version": package.version,
                "description": package.description,
                "author": package.author,
                "entrypoint": package.entrypoint,
                "tags": list(package.tags),
                "capability_names": list(package.capability_names),
                "required_tools": list(package.required_tools),
                "resources": list(package.resources),
                "dependencies": [_identity_body(item) for item in package.dependencies],
                "compatibility": _compatibility_body(package.compatibility),
                "security": dict(package.security),
                "root_path": package.root_path,
                "manifest_path": package.manifest_path,
                "manifest_sha256": package.manifest_sha256,
            }
            for package in manifest.domain_packages
        ],
        "evaluation_datasets": [
            {
                "name": dataset.name,
                "version": dataset.version,
                "description": dataset.description,
                "author": dataset.author,
                "tags": list(dataset.tags),
                "domains": [_identity_body(item) for item in dataset.domains],
                "suites": [
                    {
                        "name": suite.name,
                        "path": suite.path,
                        "description": suite.description,
                        "tags": list(suite.tags),
                    }
                    for suite in dataset.suites
                ],
                "root_path": dataset.root_path,
                "manifest_path": dataset.manifest_path,
                "manifest_sha256": dataset.manifest_sha256,
            }
            for dataset in manifest.evaluation_datasets
        ],
        "profiles": [
            {
                "name": profile.name,
                "version": profile.version,
                "description": profile.description,
                "domains": [_identity_body(item) for item in profile.domains],
                "path": profile.path,
                "config_sha256": profile.config_sha256,
            }
            for profile in manifest.profiles
        ],
    }


def decode_ecosystem_registry_manifest(payload: JsonMapping) -> EcosystemRegistryManifest:
    manifest_payload = _parse_registry_payload(_EcosystemRegistryManifestPayload, payload)
    metadata_payload = _parse_registry_payload(
        _EcosystemRegistryMetadataPayload,
        json_mapping(manifest_payload.metadata),
    )
    return EcosystemRegistryManifest(
        api_version=_api_version(manifest_payload),
        kind=manifest_payload.kind,
        name=metadata_payload.name,
        version=metadata_payload.version,
        description=metadata_payload.description,
        domain_packages=tuple(
            _domain_package_ref(item) for item in manifest_payload.domain_packages
        ),
        evaluation_datasets=tuple(
            _evaluation_dataset_ref(item) for item in manifest_payload.evaluation_datasets
        ),
        profiles=tuple(_profile_ref(item) for item in manifest_payload.profiles),
        metadata=immutable_json(json_mapping(manifest_payload.metadata)),
    )


def load_ecosystem_registry_manifest(path: str | Path) -> EcosystemRegistryManifest:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise EcosystemRegistryNotFoundError(f"ecosystem registry manifest not found: {path}")
    try:
        loaded = read_json_file(manifest_path)
    except JsonCodecError as exc:
        raise EcosystemRegistryValidationError(
            f"invalid ecosystem registry manifest JSON: {path}"
        ) from exc
    if not isinstance(loaded, dict):
        raise EcosystemRegistryValidationError("ecosystem registry manifest must be a JSON object")
    return decode_ecosystem_registry_manifest(immutable_json(loaded))


def load_ecosystem_registry_index(path: str | Path) -> EcosystemRegistryIndex:
    from universal_agent.ecosystem.registry_index import EcosystemRegistryIndex

    return EcosystemRegistryIndex(load_ecosystem_registry_manifest(path))


def write_ecosystem_registry_manifest(
    path: str | Path,
    manifest: EcosystemRegistryManifest,
    *,
    overwrite: bool = False,
) -> EcosystemRegistryWriteResult:
    output = Path(path)
    if output.exists() and not overwrite:
        raise EcosystemRegistryValidationError(
            f"ecosystem registry manifest already exists: {output}"
        )
    if output.parent != Path(""):
        output.parent.mkdir(parents=True, exist_ok=True)
    overwritten = output.exists()
    tmp_path = output.with_name(output.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        write_json(handle, encode_ecosystem_registry_manifest(manifest), indent=True)
    tmp_path.replace(output)
    return EcosystemRegistryWriteResult(manifest, output, overwritten)


def _domain_package_ref(
    payload: _EcosystemRegistryDomainPackagePayload,
) -> EcosystemDomainPackageRef:
    return EcosystemDomainPackageRef(
        name=payload.name,
        version=payload.version,
        description=payload.description,
        author=payload.author,
        entrypoint=payload.entrypoint,
        tags=_string_tuple(payload.tags, "domain_packages[].tags"),
        capability_names=_string_tuple(
            payload.capability_names,
            "domain_packages[].capability_names",
        ),
        required_tools=_string_tuple(
            payload.required_tools,
            "domain_packages[].required_tools",
        ),
        resources=_string_tuple(payload.resources, "domain_packages[].resources"),
        dependencies=_identity_tuple(payload.dependencies),
        compatibility=_compatibility(payload.compatibility),
        security=(
            immutable_json()
            if payload.security is None
            else immutable_json(json_mapping(payload.security))
        ),
        root_path=payload.root_path or "",
        manifest_path=payload.manifest_path or "",
        manifest_sha256=payload.manifest_sha256 or "",
    )


def _evaluation_dataset_ref(
    payload: _EcosystemRegistryEvaluationDatasetPayload,
) -> EcosystemEvaluationDatasetRef:
    return EcosystemEvaluationDatasetRef(
        name=payload.name,
        version=payload.version,
        description=payload.description,
        author=payload.author,
        tags=_string_tuple(payload.tags, "evaluation_datasets[].tags"),
        domains=_identity_tuple(payload.domains),
        suites=tuple(_dataset_suite_ref(suite) for suite in payload.suites),
        root_path=payload.root_path or "",
        manifest_path=payload.manifest_path or "",
        manifest_sha256=payload.manifest_sha256 or "",
    )


def _dataset_suite_ref(
    payload: _EcosystemRegistryDatasetSuitePayload,
) -> EcosystemEvaluationDatasetSuiteRef:
    return EcosystemEvaluationDatasetSuiteRef(
        name=payload.name,
        path=payload.path,
        description=payload.description or "",
        tags=_string_tuple(payload.tags, "evaluation_datasets[].suites[].tags"),
    )


def _profile_ref(payload: _EcosystemRegistryProfilePayload) -> EcosystemProfileRef:
    return EcosystemProfileRef(
        name=payload.name,
        version=payload.version,
        description=payload.description,
        domains=_identity_tuple(payload.domains),
        path=payload.path or "",
        config_sha256=payload.config_sha256 or "",
    )


def _api_version(payload: _EcosystemRegistryManifestPayload) -> str:
    camel = payload.api_version_camel
    snake = payload.api_version
    if camel is not None and snake is not None and camel != snake:
        raise EcosystemRegistryValidationError("apiVersion and api_version must match")
    value = camel if camel is not None else snake
    if value is None:
        raise EcosystemRegistryValidationError("apiVersion must be a string")
    _require_non_empty(value, "apiVersion")
    return value


def _compatibility(
    value: dict[str, PydanticJsonValue] | None,
) -> DomainPackageCompatibility:
    if value is None:
        return DomainPackageCompatibility()
    payload = _parse_registry_payload(
        _EcosystemRegistryCompatibilityPayload,
        json_mapping(value),
    )
    try:
        return DomainPackageCompatibility(
            runtime_api=payload.runtime_api,
            domain_api=payload.domain_api,
        )
    except DomainPackageValidationError as exc:
        raise EcosystemRegistryValidationError(str(exc)) from exc


def _string_tuple(value: list[str], field_name: str) -> tuple[str, ...]:
    try:
        return parse_non_empty_string_sequence(
            value,
            field_name,
            empty_template="{path} must be a non-empty string",
            item_type_template="{path} must be a non-empty string",
        )
    except ValueError as exc:
        raise EcosystemRegistryValidationError(str(exc)) from exc


def _identity_tuple(
    values: Sequence[_EcosystemRegistryIdentityPayload],
) -> tuple[DomainIdentity, ...]:
    return tuple(DomainIdentity(item.name, item.version) for item in values)


def _parse_registry_payload[T: ConfigPayload](
    model_type: type[T],
    payload: JsonMapping,
) -> T:
    try:
        return parse_payload(model_type, payload)
    except ValueError as exc:
        raise EcosystemRegistryValidationError(str(exc)) from exc


def _identity_body(identity: DomainIdentity) -> dict[str, str]:
    return {"name": identity.name, "version": identity.version}


def _compatibility_body(compatibility: DomainPackageCompatibility) -> dict[str, str]:
    body: dict[str, str] = {}
    if compatibility.runtime_api is not None:
        body["runtime_api"] = compatibility.runtime_api
    if compatibility.domain_api is not None:
        body["domain_api"] = compatibility.domain_api
    return body
