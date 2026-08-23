from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from universal_agent.core import DomainIdentity, JsonMapping, immutable_json
from universal_agent.domain import DomainPackage, DomainPackageRegistry
from universal_agent.evaluation.dataset import EvaluationDataset, EvaluationDatasetRegistry
from universal_agent.profile import ProfileCatalog, ProfileCatalogEntry

ECOSYSTEM_REGISTRY_KIND = "EcosystemRegistry"


@dataclass(frozen=True, slots=True)
class EcosystemCatalogSummary:
    domain_package_count: int
    evaluation_dataset_count: int
    profile_count: int

    @property
    def total_items(self) -> int:
        return self.domain_package_count + self.evaluation_dataset_count + self.profile_count


@dataclass(frozen=True, slots=True)
class EcosystemCatalogCheck:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class EcosystemCatalogVerificationReport:
    checks: tuple[EcosystemCatalogCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed_checks(self) -> tuple[EcosystemCatalogCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)


class EcosystemRegistryValidationError(ValueError):
    pass


class EcosystemRegistryNotFoundError(LookupError):
    pass


class EcosystemRegistryItemNotFoundError(LookupError):
    pass


class AmbiguousEcosystemRegistryItemError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EcosystemDomainPackageRef:
    name: str
    version: str
    description: str
    author: str | None = None
    tags: tuple[str, ...] = ()
    capability_names: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    dependencies: tuple[DomainIdentity, ...] = ()
    root_path: str = ""
    manifest_path: str = ""

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "domain_packages[].name")
        _require_non_empty(self.version, "domain_packages[].version")
        _require_non_empty(self.description, "domain_packages[].description")
        _validate_strings("domain_packages[].tags", self.tags)
        _validate_strings("domain_packages[].capability_names", self.capability_names)
        _validate_strings("domain_packages[].required_tools", self.required_tools)
        for index, dependency in enumerate(self.dependencies):
            _require_non_empty(
                dependency.name,
                f"domain_packages[].dependencies[{index}].name",
            )
            _require_non_empty(
                dependency.version,
                f"domain_packages[].dependencies[{index}].version",
            )

    @property
    def identity(self) -> DomainIdentity:
        return DomainIdentity(self.name, self.version)


@dataclass(frozen=True, slots=True)
class EcosystemEvaluationDatasetSuiteRef:
    name: str
    path: str
    description: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "evaluation_datasets[].suites[].name")
        _require_non_empty(self.path, "evaluation_datasets[].suites[].path")
        _validate_strings("evaluation_datasets[].suites[].tags", self.tags)


@dataclass(frozen=True, slots=True)
class EcosystemEvaluationDatasetRef:
    name: str
    version: str
    description: str
    author: str | None = None
    tags: tuple[str, ...] = ()
    domains: tuple[DomainIdentity, ...] = ()
    suites: tuple[EcosystemEvaluationDatasetSuiteRef, ...] = ()
    root_path: str = ""
    manifest_path: str = ""

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "evaluation_datasets[].name")
        _require_non_empty(self.version, "evaluation_datasets[].version")
        _require_non_empty(self.description, "evaluation_datasets[].description")
        _validate_strings("evaluation_datasets[].tags", self.tags)
        if not self.suites:
            raise EcosystemRegistryValidationError(
                "evaluation_datasets[] must include at least one suite"
            )
        for index, domain in enumerate(self.domains):
            _require_non_empty(domain.name, f"evaluation_datasets[].domains[{index}].name")
            _require_non_empty(domain.version, f"evaluation_datasets[].domains[{index}].version")

    @property
    def identity(self) -> tuple[str, str]:
        return (self.name, self.version)


@dataclass(frozen=True, slots=True)
class EcosystemProfileRef:
    name: str
    version: str
    description: str
    domains: tuple[DomainIdentity, ...]
    path: str

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "profiles[].name")
        _require_non_empty(self.version, "profiles[].version")
        if not self.domains:
            raise EcosystemRegistryValidationError("profiles[] must include at least one domain")
        for index, domain in enumerate(self.domains):
            _require_non_empty(domain.name, f"profiles[].domains[{index}].name")
            _require_non_empty(domain.version, f"profiles[].domains[{index}].version")

    @property
    def identity(self) -> tuple[str, str]:
        return (self.name, self.version)


@dataclass(frozen=True, slots=True)
class EcosystemRegistryManifest:
    api_version: str
    kind: str
    name: str
    version: str
    description: str
    domain_packages: tuple[EcosystemDomainPackageRef, ...] = ()
    evaluation_datasets: tuple[EcosystemEvaluationDatasetRef, ...] = ()
    profiles: tuple[EcosystemProfileRef, ...] = ()
    metadata: JsonMapping = field(default_factory=immutable_json)

    def __post_init__(self) -> None:
        _require_non_empty(self.api_version, "apiVersion")
        _require_non_empty(self.kind, "kind")
        _require_non_empty(self.name, "metadata.name")
        _require_non_empty(self.version, "metadata.version")
        _require_non_empty(self.description, "metadata.description")
        if self.kind != ECOSYSTEM_REGISTRY_KIND:
            raise EcosystemRegistryValidationError(f"kind must be {ECOSYSTEM_REGISTRY_KIND}")
        _reject_duplicates(
            "domain package",
            tuple(_format_domain_identity(item.identity) for item in self.domain_packages),
        )
        _reject_duplicates(
            "evaluation dataset",
            tuple(f"{name}@{version}" for name, version in _dataset_identities(self)),
        )
        _reject_duplicates(
            "profile",
            tuple(f"{name}@{version}" for name, version in _profile_identities(self)),
        )

    @property
    def summary(self) -> EcosystemCatalogSummary:
        return EcosystemCatalogSummary(
            domain_package_count=len(self.domain_packages),
            evaluation_dataset_count=len(self.evaluation_datasets),
            profile_count=len(self.profiles),
        )


@dataclass(frozen=True, slots=True)
class EcosystemRegistryWriteResult:
    manifest: EcosystemRegistryManifest
    path: Path
    overwritten: bool


@dataclass(frozen=True, slots=True)
class EcosystemRegistryIndex:
    """Read-only query index over an exported ecosystem registry manifest.

    The index is intentionally metadata-only. It never imports Domain entrypoints,
    executes evaluation suites or assembles RuntimeHost instances.
    """

    manifest: EcosystemRegistryManifest

    @property
    def summary(self) -> EcosystemCatalogSummary:
        return self.manifest.summary

    def domain_packages(self, *, tag: str | None = None) -> tuple[EcosystemDomainPackageRef, ...]:
        packages = self.manifest.domain_packages
        if tag is None:
            return packages
        return tuple(package for package in packages if tag in package.tags)

    def domain_package(
        self,
        name: str,
        version: str | None = None,
    ) -> EcosystemDomainPackageRef:
        matches = tuple(
            package
            for package in self.manifest.domain_packages
            if package.name == name and (version is None or package.version == version)
        )
        if not matches:
            raise EcosystemRegistryItemNotFoundError(
                _missing_registry_item_message("domain package", name, version)
            )
        if len(matches) > 1:
            raise AmbiguousEcosystemRegistryItemError(
                _ambiguous_registry_item_message("domain package", name, matches)
            )
        return matches[0]

    def evaluation_datasets(
        self,
        *,
        tag: str | None = None,
        domain: DomainIdentity | None = None,
    ) -> tuple[EcosystemEvaluationDatasetRef, ...]:
        datasets = self.manifest.evaluation_datasets
        if tag is not None:
            datasets = tuple(dataset for dataset in datasets if tag in dataset.tags)
        if domain is not None:
            datasets = tuple(dataset for dataset in datasets if domain in dataset.domains)
        return datasets

    def evaluation_dataset(
        self,
        name: str,
        version: str | None = None,
    ) -> EcosystemEvaluationDatasetRef:
        matches = tuple(
            dataset
            for dataset in self.manifest.evaluation_datasets
            if dataset.name == name and (version is None or dataset.version == version)
        )
        if not matches:
            raise EcosystemRegistryItemNotFoundError(
                _missing_registry_item_message("evaluation dataset", name, version)
            )
        if len(matches) > 1:
            raise AmbiguousEcosystemRegistryItemError(
                _ambiguous_registry_item_message("evaluation dataset", name, matches)
            )
        return matches[0]

    def profiles(
        self,
        *,
        domain: DomainIdentity | None = None,
    ) -> tuple[EcosystemProfileRef, ...]:
        profiles = self.manifest.profiles
        if domain is None:
            return profiles
        return tuple(profile for profile in profiles if domain in profile.domains)

    def profile(self, name: str, version: str | None = None) -> EcosystemProfileRef:
        matches = tuple(
            profile
            for profile in self.manifest.profiles
            if profile.name == name and (version is None or profile.version == version)
        )
        if not matches:
            raise EcosystemRegistryItemNotFoundError(
                _missing_registry_item_message("profile", name, version)
            )
        if len(matches) > 1:
            raise AmbiguousEcosystemRegistryItemError(
                _ambiguous_registry_item_message("profile", name, matches)
            )
        return matches[0]


@dataclass(frozen=True, slots=True)
class EcosystemCatalog:
    """P7 local ecosystem index across packages, datasets and profiles.

    This module is deliberately a metadata aggregator. It validates and indexes
    existing ecosystem artifacts but does not activate Domain runtimes, run
    evaluation scenarios or build RuntimeHost instances.
    """

    domain_packages: tuple[DomainPackage, ...] = ()
    evaluation_datasets: tuple[EvaluationDataset, ...] = ()
    profiles: tuple[ProfileCatalogEntry, ...] = ()

    @classmethod
    def discover(
        cls,
        *,
        domain_package_root: str | Path | None = None,
        evaluation_dataset_root: str | Path | None = None,
        profile_root: str | Path | None = None,
    ) -> EcosystemCatalog:
        return cls(
            domain_packages=_discover_domain_packages(domain_package_root),
            evaluation_datasets=_discover_evaluation_datasets(evaluation_dataset_root),
            profiles=_discover_profiles(profile_root),
        )

    @property
    def summary(self) -> EcosystemCatalogSummary:
        return EcosystemCatalogSummary(
            domain_package_count=len(self.domain_packages),
            evaluation_dataset_count=len(self.evaluation_datasets),
            profile_count=len(self.profiles),
        )

    def verify(self) -> EcosystemCatalogVerificationReport:
        registered_domains = frozenset(package.identity for package in self.domain_packages)
        return EcosystemCatalogVerificationReport(
            (
                _profile_domains_registered(self.profiles, registered_domains),
                _dataset_domains_registered(self.evaluation_datasets, registered_domains),
                _package_dependencies_registered(self.domain_packages, registered_domains),
            )
        )

    def registry_manifest(
        self,
        *,
        name: str = "local-ecosystem",
        version: str = "0.1.0",
        description: str = "Local Universal Agent ecosystem registry",
        api_version: str = "agent.nantian.dev/v1alpha1",
        metadata: JsonMapping | None = None,
    ) -> EcosystemRegistryManifest:
        return build_ecosystem_registry_manifest(
            self,
            name=name,
            version=version,
            description=description,
            api_version=api_version,
            metadata=metadata,
        )


def load_ecosystem_catalog(
    *,
    domain_package_root: str | Path | None = None,
    evaluation_dataset_root: str | Path | None = None,
    profile_root: str | Path | None = None,
) -> EcosystemCatalog:
    return EcosystemCatalog.discover(
        domain_package_root=domain_package_root,
        evaluation_dataset_root=evaluation_dataset_root,
        profile_root=profile_root,
    )


def build_ecosystem_registry_manifest(
    catalog: EcosystemCatalog,
    *,
    name: str = "local-ecosystem",
    version: str = "0.1.0",
    description: str = "Local Universal Agent ecosystem registry",
    api_version: str = "agent.nantian.dev/v1alpha1",
    metadata: JsonMapping | None = None,
) -> EcosystemRegistryManifest:
    return EcosystemRegistryManifest(
        api_version=api_version,
        kind=ECOSYSTEM_REGISTRY_KIND,
        name=name,
        version=version,
        description=description,
        domain_packages=tuple(_domain_package_ref(package) for package in catalog.domain_packages),
        evaluation_datasets=tuple(
            _evaluation_dataset_ref(dataset) for dataset in catalog.evaluation_datasets
        ),
        profiles=tuple(_profile_ref(entry) for entry in catalog.profiles),
        metadata=immutable_json(metadata),
    )


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
                "tags": list(package.tags),
                "capability_names": list(package.capability_names),
                "required_tools": list(package.required_tools),
                "dependencies": [_identity_body(item) for item in package.dependencies],
                "root_path": package.root_path,
                "manifest_path": package.manifest_path,
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


def _discover_domain_packages(root: str | Path | None) -> tuple[DomainPackage, ...]:
    if root is None:
        return ()
    registry = DomainPackageRegistry()
    registry.discover(Path(root))
    return registry.list()


def _discover_evaluation_datasets(root: str | Path | None) -> tuple[EvaluationDataset, ...]:
    if root is None:
        return ()
    registry = EvaluationDatasetRegistry()
    registry.discover(Path(root))
    return registry.list()


def _discover_profiles(root: str | Path | None) -> tuple[ProfileCatalogEntry, ...]:
    if root is None:
        return ()
    return ProfileCatalog.discover(root).all()


def _domain_package_ref(package: DomainPackage) -> EcosystemDomainPackageRef:
    manifest = package.manifest
    return EcosystemDomainPackageRef(
        name=package.identity.name,
        version=package.identity.version,
        description=manifest.description,
        author=manifest.author,
        tags=manifest.tags,
        capability_names=manifest.capabilities,
        required_tools=manifest.required_tools,
        dependencies=manifest.dependencies,
        root_path=str(package.root_path),
        manifest_path=str(package.manifest_path),
    )


def _evaluation_dataset_ref(dataset: EvaluationDataset) -> EcosystemEvaluationDatasetRef:
    return EcosystemEvaluationDatasetRef(
        name=dataset.identity.name,
        version=dataset.identity.version,
        description=dataset.manifest.description,
        author=dataset.manifest.author,
        tags=dataset.manifest.tags,
        domains=dataset.manifest.domains,
        suites=tuple(
            EcosystemEvaluationDatasetSuiteRef(
                name=suite.name,
                path=suite.path,
                description=suite.description,
                tags=suite.tags,
            )
            for suite in dataset.manifest.suites
        ),
        root_path=str(dataset.root_path),
        manifest_path=str(dataset.manifest_path),
    )


def _profile_ref(entry: ProfileCatalogEntry) -> EcosystemProfileRef:
    profile = entry.profile
    return EcosystemProfileRef(
        name=profile.name,
        version=profile.version,
        description=profile.description,
        domains=tuple(
            DomainIdentity(domain.name or "", domain.version or "")
            for domain in profile.configured_domains()
        ),
        path=str(entry.path),
    )


def _profile_domains_registered(
    profiles: tuple[ProfileCatalogEntry, ...],
    registered_domains: frozenset[DomainIdentity],
) -> EcosystemCatalogCheck:
    missing = tuple(
        f"{entry.profile.name}:{domain.name}@{domain.version}"
        for entry in profiles
        for domain in _profile_domain_identities(entry)
        if domain not in registered_domains
    )
    return _reference_check(
        "profile_domains_registered",
        missing,
        "all profile domains are backed by discovered Domain packages",
        "profiles reference missing Domain packages",
    )


def _dataset_domains_registered(
    datasets: tuple[EvaluationDataset, ...],
    registered_domains: frozenset[DomainIdentity],
) -> EcosystemCatalogCheck:
    missing = tuple(
        f"{dataset.identity.name}:{domain.name}@{domain.version}"
        for dataset in datasets
        for domain in dataset.manifest.domains
        if domain not in registered_domains
    )
    return _reference_check(
        "dataset_domains_registered",
        missing,
        "all evaluation dataset domains are backed by discovered Domain packages",
        "evaluation datasets reference missing Domain packages",
    )


def _profile_domain_identities(entry: ProfileCatalogEntry) -> tuple[DomainIdentity, ...]:
    return tuple(
        DomainIdentity(domain.name or "", domain.version or "")
        for domain in entry.profile.configured_domains()
    )


def _package_dependencies_registered(
    packages: tuple[DomainPackage, ...],
    registered_domains: frozenset[DomainIdentity],
) -> EcosystemCatalogCheck:
    missing = tuple(
        f"{package.identity.name}:{dependency.name}@{dependency.version}"
        for package in packages
        for dependency in package.manifest.dependencies
        if dependency not in registered_domains
    )
    return _reference_check(
        "package_dependencies_registered",
        missing,
        "all Domain package dependencies are present in the catalog",
        "Domain packages reference missing dependencies",
    )


def _reference_check(
    name: str,
    missing: tuple[str, ...],
    passed_message: str,
    failed_prefix: str,
) -> EcosystemCatalogCheck:
    if not missing:
        return EcosystemCatalogCheck(name, True, passed_message)
    return EcosystemCatalogCheck(name, False, f"{failed_prefix}: {', '.join(missing)}")


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
            dependencies=_identity_tuple(
                item,
                "dependencies",
                field_name=f"domain_packages[{index}].dependencies",
            ),
            root_path=_optional_string(
                item,
                "root_path",
                field_name=f"domain_packages[{index}].root_path",
            )
            or "",
            manifest_path=_optional_string(
                item,
                "manifest_path",
                field_name=f"domain_packages[{index}].manifest_path",
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
            root_path=_optional_string(
                item,
                "root_path",
                field_name=f"evaluation_datasets[{index}].root_path",
            )
            or "",
            manifest_path=_optional_string(
                item,
                "manifest_path",
                field_name=f"evaluation_datasets[{index}].manifest_path",
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
            path=_optional_string(item, "path", field_name=f"profiles[{index}].path") or "",
        )
        for index, item in enumerate(_object_list(payload, "profiles"))
    )


def _mapping(payload: JsonMapping, key: str) -> JsonMapping:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise EcosystemRegistryValidationError(f"{key} must be an object")
    return immutable_json(value)


def _api_version(payload: JsonMapping) -> str:
    camel = payload.get("apiVersion")
    snake = payload.get("api_version")
    if camel is not None and snake is not None and camel != snake:
        raise EcosystemRegistryValidationError("apiVersion and api_version must match")
    value = camel if camel is not None else snake
    return _string_value(value, "apiVersion")


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


def _dataset_identities(
    manifest: EcosystemRegistryManifest,
) -> tuple[tuple[str, str], ...]:
    return tuple(dataset.identity for dataset in manifest.evaluation_datasets)


def _profile_identities(
    manifest: EcosystemRegistryManifest,
) -> tuple[tuple[str, str], ...]:
    return tuple(profile.identity for profile in manifest.profiles)


def _reject_duplicates(label: str, identities: tuple[str, ...]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for identity in identities:
        if identity in seen:
            duplicates.add(identity)
        seen.add(identity)
    if duplicates:
        raise EcosystemRegistryValidationError(
            f"duplicate {label} references: {', '.join(sorted(duplicates))}"
        )


def _missing_registry_item_message(label: str, name: str, version: str | None) -> str:
    if version is None:
        return f"{label} not found in ecosystem registry: {name}"
    return f"{label} not found in ecosystem registry: {name}@{version}"


def _ambiguous_registry_item_message(
    label: str,
    name: str,
    matches: tuple[
        EcosystemDomainPackageRef | EcosystemEvaluationDatasetRef | EcosystemProfileRef,
        ...,
    ],
) -> str:
    versions = ", ".join(sorted(item.version for item in matches))
    return f"{label} {name} has multiple versions in ecosystem registry: {versions}"


def _format_domain_identity(identity: DomainIdentity) -> str:
    return f"{identity.name}@{identity.version}"


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise EcosystemRegistryValidationError(f"{field_name} must not be empty")


def _validate_strings(field_name: str, values: tuple[str, ...]) -> None:
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            raise EcosystemRegistryValidationError(
                f"{field_name}[{index}] must be a non-empty string"
            )


__all__ = [
    "AmbiguousEcosystemRegistryItemError",
    "EcosystemCatalog",
    "EcosystemCatalogCheck",
    "EcosystemCatalogSummary",
    "EcosystemCatalogVerificationReport",
    "EcosystemDomainPackageRef",
    "EcosystemEvaluationDatasetRef",
    "EcosystemEvaluationDatasetSuiteRef",
    "EcosystemProfileRef",
    "EcosystemRegistryIndex",
    "EcosystemRegistryItemNotFoundError",
    "EcosystemRegistryManifest",
    "EcosystemRegistryNotFoundError",
    "EcosystemRegistryValidationError",
    "EcosystemRegistryWriteResult",
    "build_ecosystem_registry_manifest",
    "decode_ecosystem_registry_manifest",
    "encode_ecosystem_registry_manifest",
    "load_ecosystem_catalog",
    "load_ecosystem_registry_index",
    "load_ecosystem_registry_manifest",
    "write_ecosystem_registry_manifest",
]
