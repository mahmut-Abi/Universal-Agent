from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from universal_agent.core import DomainIdentity, JsonMapping, immutable_json
from universal_agent.domain import DomainPackageCompatibility
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
    metadata = _mapping(payload, "metadata")
    return EcosystemRegistryManifest(
        api_version=_api_version(payload),
        kind=_string(payload, "kind"),
        name=_string(metadata, "name", field_name="metadata.name"),
        version=_string(metadata, "version", field_name="metadata.version"),
        description=_string(metadata, "description", field_name="metadata.description"),
        domain_packages=_domain_package_refs(payload),
        evaluation_datasets=_evaluation_dataset_refs(payload),
        profiles=_profile_refs(payload),
        metadata=immutable_json(metadata),
    )


def load_ecosystem_registry_manifest(path: str | Path) -> EcosystemRegistryManifest:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise EcosystemRegistryNotFoundError(f"ecosystem registry manifest not found: {path}")
    try:
        loaded: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
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
        json.dump(encode_ecosystem_registry_manifest(manifest), handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(output)
    return EcosystemRegistryWriteResult(manifest, output, overwritten)


def _domain_package_refs(payload: JsonMapping) -> tuple[EcosystemDomainPackageRef, ...]:
    return tuple(
        EcosystemDomainPackageRef(
            name=_string(item, "name", field_name=f"domain_packages[{index}].name"),
            version=_string(item, "version", field_name=f"domain_packages[{index}].version"),
            description=_string(
                item,
                "description",
                field_name=f"domain_packages[{index}].description",
            ),
            author=_optional_string(item, "author", field_name=f"domain_packages[{index}].author"),
            entrypoint=_optional_string(
                item,
                "entrypoint",
                field_name=f"domain_packages[{index}].entrypoint",
            ),
            tags=_string_tuple(item, "tags", field_name=f"domain_packages[{index}].tags"),
            capability_names=_string_tuple(
                item,
                "capability_names",
                field_name=f"domain_packages[{index}].capability_names",
            ),
            required_tools=_string_tuple(
                item,
                "required_tools",
                field_name=f"domain_packages[{index}].required_tools",
            ),
            resources=_string_tuple(
                item,
                "resources",
                field_name=f"domain_packages[{index}].resources",
            ),
            dependencies=_identity_tuple(
                item,
                "dependencies",
                field_name=f"domain_packages[{index}].dependencies",
            ),
            compatibility=_compatibility(
                item,
                field_name=f"domain_packages[{index}].compatibility",
            ),
            security=_optional_mapping(
                item,
                "security",
                field_name=f"domain_packages[{index}].security",
            )
            or immutable_json(),
            root_path=_optional_string_allow_empty(
                item,
                "root_path",
                field_name=f"domain_packages[{index}].root_path",
            )
            or "",
            manifest_path=_optional_string_allow_empty(
                item,
                "manifest_path",
                field_name=f"domain_packages[{index}].manifest_path",
            )
            or "",
            manifest_sha256=_optional_string_allow_empty(
                item,
                "manifest_sha256",
                field_name=f"domain_packages[{index}].manifest_sha256",
            )
            or "",
        )
        for index, item in enumerate(_object_list(payload, "domain_packages"))
    )


def _evaluation_dataset_refs(payload: JsonMapping) -> tuple[EcosystemEvaluationDatasetRef, ...]:
    return tuple(
        EcosystemEvaluationDatasetRef(
            name=_string(item, "name", field_name=f"evaluation_datasets[{index}].name"),
            version=_string(item, "version", field_name=f"evaluation_datasets[{index}].version"),
            description=_string(
                item,
                "description",
                field_name=f"evaluation_datasets[{index}].description",
            ),
            author=_optional_string(
                item,
                "author",
                field_name=f"evaluation_datasets[{index}].author",
            ),
            tags=_string_tuple(item, "tags", field_name=f"evaluation_datasets[{index}].tags"),
            domains=_identity_tuple(
                item,
                "domains",
                field_name=f"evaluation_datasets[{index}].domains",
            ),
            suites=_dataset_suite_refs(item, f"evaluation_datasets[{index}].suites"),
            root_path=_optional_string_allow_empty(
                item,
                "root_path",
                field_name=f"evaluation_datasets[{index}].root_path",
            )
            or "",
            manifest_path=_optional_string_allow_empty(
                item,
                "manifest_path",
                field_name=f"evaluation_datasets[{index}].manifest_path",
            )
            or "",
            manifest_sha256=_optional_string_allow_empty(
                item,
                "manifest_sha256",
                field_name=f"evaluation_datasets[{index}].manifest_sha256",
            )
            or "",
        )
        for index, item in enumerate(_object_list(payload, "evaluation_datasets"))
    )


def _dataset_suite_refs(
    payload: JsonMapping,
    field_name: str,
) -> tuple[EcosystemEvaluationDatasetSuiteRef, ...]:
    return tuple(
        EcosystemEvaluationDatasetSuiteRef(
            name=_string(item, "name", field_name=f"{field_name}[{index}].name"),
            path=_string(item, "path", field_name=f"{field_name}[{index}].path"),
            description=_optional_string_allow_empty(
                item,
                "description",
                field_name=f"{field_name}[{index}].description",
            )
            or "",
            tags=_string_tuple(item, "tags", field_name=f"{field_name}[{index}].tags"),
        )
        for index, item in enumerate(_object_list(payload, "suites", field_name=field_name))
    )


def _profile_refs(payload: JsonMapping) -> tuple[EcosystemProfileRef, ...]:
    return tuple(
        EcosystemProfileRef(
            name=_string(item, "name", field_name=f"profiles[{index}].name"),
            version=_string(item, "version", field_name=f"profiles[{index}].version"),
            description=_string_allow_empty(
                item, "description", field_name=f"profiles[{index}].description"
            ),
            domains=_identity_tuple(item, "domains", field_name=f"profiles[{index}].domains"),
            path=_optional_string_allow_empty(item, "path", field_name=f"profiles[{index}].path")
            or "",
            config_sha256=_optional_string_allow_empty(
                item,
                "config_sha256",
                field_name=f"profiles[{index}].config_sha256",
            )
            or "",
        )
        for index, item in enumerate(_object_list(payload, "profiles"))
    )


def _mapping(payload: JsonMapping, key: str) -> JsonMapping:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise EcosystemRegistryValidationError(f"{key} must be an object")
    return immutable_json(value)


def _optional_mapping(
    payload: JsonMapping,
    key: str,
    *,
    field_name: str | None = None,
) -> JsonMapping | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise EcosystemRegistryValidationError(f"{field_name or key} must be an object")
    return immutable_json(value)


def _api_version(payload: JsonMapping) -> str:
    camel = payload.get("apiVersion")
    snake = payload.get("api_version")
    if camel is not None and snake is not None and camel != snake:
        raise EcosystemRegistryValidationError("apiVersion and api_version must match")
    value = camel if camel is not None else snake
    return _string_value(value, "apiVersion")


def _compatibility(
    payload: JsonMapping,
    *,
    field_name: str,
) -> DomainPackageCompatibility:
    compatibility = _optional_mapping(payload, "compatibility", field_name=field_name)
    if compatibility is None:
        return DomainPackageCompatibility()
    return DomainPackageCompatibility(
        runtime_api=_optional_string(
            compatibility,
            "runtime_api",
            field_name=f"{field_name}.runtime_api",
        ),
        domain_api=_optional_string(
            compatibility,
            "domain_api",
            field_name=f"{field_name}.domain_api",
        ),
    )


def _object_list(
    payload: JsonMapping,
    key: str,
    *,
    field_name: str | None = None,
) -> tuple[JsonMapping, ...]:
    value = payload.get(key, ())
    if not isinstance(value, list | tuple):
        raise EcosystemRegistryValidationError(f"{field_name or key} must be a list")
    items: list[JsonMapping] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise EcosystemRegistryValidationError(
                f"{field_name or key}[{index}] must be an object"
            )
        items.append(immutable_json(item))
    return tuple(items)


def _string(payload: JsonMapping, key: str, *, field_name: str | None = None) -> str:
    return _string_value(payload.get(key), field_name or key)


def _string_allow_empty(
    payload: JsonMapping,
    key: str,
    *,
    field_name: str | None = None,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise EcosystemRegistryValidationError(f"{field_name or key} must be a string")
    return value


def _optional_string_allow_empty(
    payload: JsonMapping,
    key: str,
    *,
    field_name: str | None = None,
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise EcosystemRegistryValidationError(f"{field_name or key} must be a string")
    return value


def _string_value(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise EcosystemRegistryValidationError(f"{field_name} must be a string")
    _require_non_empty(value, field_name)
    return value


def _optional_string(
    payload: JsonMapping,
    key: str,
    *,
    field_name: str | None = None,
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    return _string_value(value, field_name or key)


def _string_tuple(
    payload: JsonMapping,
    key: str,
    *,
    field_name: str | None = None,
) -> tuple[str, ...]:
    value = payload.get(key, ())
    if not isinstance(value, list | tuple):
        raise EcosystemRegistryValidationError(f"{field_name or key} must be a list of strings")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise EcosystemRegistryValidationError(
                f"{field_name or key}[{index}] must be a non-empty string"
            )
        items.append(item)
    return tuple(items)


def _identity_tuple(
    payload: JsonMapping,
    key: str,
    *,
    field_name: str | None = None,
) -> tuple[DomainIdentity, ...]:
    value = payload.get(key, ())
    if not isinstance(value, list | tuple):
        raise EcosystemRegistryValidationError(
            f"{field_name or key} must be a list of domain identity objects"
        )
    identities: list[DomainIdentity] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise EcosystemRegistryValidationError(
                f"{field_name or key}[{index}] must be an object"
            )
        identity = immutable_json(item)
        identities.append(
            DomainIdentity(
                _string(identity, "name", field_name=f"{field_name or key}[{index}].name"),
                _string(identity, "version", field_name=f"{field_name or key}[{index}].version"),
            )
        )
    return tuple(identities)


def _identity_body(identity: DomainIdentity) -> dict[str, str]:
    return {"name": identity.name, "version": identity.version}


def _compatibility_body(compatibility: DomainPackageCompatibility) -> dict[str, str]:
    body: dict[str, str] = {}
    if compatibility.runtime_api is not None:
        body["runtime_api"] = compatibility.runtime_api
    if compatibility.domain_api is not None:
        body["domain_api"] = compatibility.domain_api
    return body


