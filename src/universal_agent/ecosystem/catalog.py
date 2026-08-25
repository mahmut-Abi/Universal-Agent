from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from universal_agent.core import DomainIdentity, JsonMapping, immutable_json
from universal_agent.domain import (
    DomainPackage,
    DomainPackageCompatibility,
    DomainPackageRegistry,
    load_domain_package,
    verify_domain_package,
)
from universal_agent.evaluation.dataset import (
    EvaluationDataset,
    EvaluationDatasetRegistry,
    load_evaluation_dataset,
)
from universal_agent.profile import (
    ProfileCatalog,
    ProfileCatalogEntry,
    ProfileConfig,
    ProfileRegistry,
)

ECOSYSTEM_REGISTRY_KIND = "EcosystemRegistry"


class _DatasetSuiteLike(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def path(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def tags(self) -> tuple[str, ...]: ...


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


class EcosystemRegistryStoreNotFoundError(LookupError):
    pass


class EcosystemRegistryInstallError(ValueError):
    pass


class AmbiguousEcosystemRegistryItemError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EcosystemRegistryTrustPolicy:
    """Trust policy for local registry install planning.

    The local P7 registry foundation has no built-in cryptographic verifier. A
    registry that declares signature metadata is rejected by default unless a
    caller supplies an explicit signature verifier or opts into unverified local
    trust for trusted registries.
    """

    allow_unsigned: bool = True
    allow_unverified_signatures: bool = False


@dataclass(frozen=True, slots=True)
class EcosystemRegistrySignatureVerification:
    passed: bool
    verifier: str
    reason: str
    signer: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.verifier, "signature_verification.verifier")
        _require_non_empty(self.reason, "signature_verification.reason")
        if self.signer is not None:
            _require_non_empty(self.signer, "signature_verification.signer")


class EcosystemRegistrySignatureVerifier(Protocol):
    """Verifies registry signature metadata before local install planning."""

    def verify_registry(
        self,
        manifest: EcosystemRegistryManifest,
    ) -> EcosystemRegistrySignatureVerification: ...


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
    compatibility: DomainPackageCompatibility = field(default_factory=DomainPackageCompatibility)
    security: JsonMapping = field(default_factory=immutable_json)
    root_path: str = ""
    manifest_path: str = ""
    manifest_sha256: str = ""
    entrypoint: str | None = None
    resources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "domain_packages[].name")
        _require_non_empty(self.version, "domain_packages[].version")
        _require_non_empty(self.description, "domain_packages[].description")
        if self.entrypoint is not None:
            _require_non_empty(self.entrypoint, "domain_packages[].entrypoint")
        _validate_strings("domain_packages[].tags", self.tags)
        _validate_strings("domain_packages[].capability_names", self.capability_names)
        _validate_strings("domain_packages[].required_tools", self.required_tools)
        _validate_strings("domain_packages[].resources", self.resources)
        _validate_optional_sha256("domain_packages[].manifest_sha256", self.manifest_sha256)
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
    manifest_sha256: str = ""

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "evaluation_datasets[].name")
        _require_non_empty(self.version, "evaluation_datasets[].version")
        _require_non_empty(self.description, "evaluation_datasets[].description")
        _validate_strings("evaluation_datasets[].tags", self.tags)
        _validate_optional_sha256(
            "evaluation_datasets[].manifest_sha256",
            self.manifest_sha256,
        )
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
    config_sha256: str = ""

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "profiles[].name")
        _require_non_empty(self.version, "profiles[].version")
        if not self.domains:
            raise EcosystemRegistryValidationError("profiles[] must include at least one domain")
        _validate_optional_sha256("profiles[].config_sha256", self.config_sha256)
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
class EcosystemDomainPackageInstallCandidate:
    reference: EcosystemDomainPackageRef
    package: DomainPackage


@dataclass(frozen=True, slots=True)
class EcosystemDomainPackageInstallPlan:
    candidates: tuple[EcosystemDomainPackageInstallCandidate, ...]

    @property
    def packages(self) -> tuple[DomainPackage, ...]:
        return tuple(candidate.package for candidate in self.candidates)

    @property
    def identities(self) -> tuple[DomainIdentity, ...]:
        return tuple(package.identity for package in self.packages)


@dataclass(frozen=True, slots=True)
class EcosystemDomainPackageInstallResult:
    registry: DomainPackageRegistry
    installed_packages: tuple[DomainPackage, ...]


@dataclass(frozen=True, slots=True)
class EcosystemEvaluationDatasetInstallCandidate:
    reference: EcosystemEvaluationDatasetRef
    dataset: EvaluationDataset


@dataclass(frozen=True, slots=True)
class EcosystemProfileInstallCandidate:
    reference: EcosystemProfileRef
    entry: ProfileCatalogEntry


@dataclass(frozen=True, slots=True)
class EcosystemInstallPlan:
    domain_packages: EcosystemDomainPackageInstallPlan
    evaluation_datasets: tuple[EcosystemEvaluationDatasetInstallCandidate, ...] = ()
    profiles: tuple[EcosystemProfileInstallCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class EcosystemInstallResult:
    domain_packages: DomainPackageRegistry
    evaluation_datasets: EvaluationDatasetRegistry
    profiles: ProfileRegistry
    installed_domain_packages: tuple[DomainPackage, ...]
    installed_evaluation_datasets: tuple[EvaluationDataset, ...]
    installed_profiles: tuple[ProfileCatalogEntry, ...]


class FileEcosystemRegistryStore:
    """File-backed store for exported ecosystem registry manifests.

    The store is a local package-registry primitive. It persists registry
    manifests and lists them by metadata identity, but it does not inspect
    package roots, import Domain code, run evaluation suites or assemble hosts.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def save(
        self,
        manifest: EcosystemRegistryManifest,
        *,
        overwrite: bool = True,
    ) -> EcosystemRegistryWriteResult:
        return write_ecosystem_registry_manifest(
            self._path(manifest.name, manifest.version),
            manifest,
            overwrite=overwrite,
        )

    def load(self, name: str, version: str) -> EcosystemRegistryManifest:
        path = self._path(name, version)
        if not path.exists():
            raise EcosystemRegistryStoreNotFoundError(
                f"ecosystem registry manifest not found: {name}@{version}"
            )
        return load_ecosystem_registry_manifest(path)

    def index(self, name: str, version: str) -> EcosystemRegistryIndex:
        return EcosystemRegistryIndex(self.load(name, version))

    def list_manifests(self) -> tuple[EcosystemRegistryManifest, ...]:
        if not self._root.exists():
            return ()
        manifests = tuple(
            load_ecosystem_registry_manifest(path) for path in sorted(self._root.glob("*.json"))
        )
        return tuple(sorted(manifests, key=lambda item: (item.name, item.version)))

    def _path(self, name: str, version: str) -> Path:
        _require_non_empty(name, "registry name")
        _require_non_empty(version, "registry version")
        return self._root / f"{quote(name, safe='')}@{quote(version, safe='')}.json"


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

    def verify(self) -> EcosystemCatalogVerificationReport:
        registered_domains = frozenset(
            package.identity for package in self.manifest.domain_packages
        )
        return EcosystemCatalogVerificationReport(
            (
                _registry_profile_domains_registered(self.manifest.profiles, registered_domains),
                _registry_dataset_domains_registered(
                    self.manifest.evaluation_datasets,
                    registered_domains,
                ),
                _registry_package_dependencies_registered(
                    self.manifest.domain_packages,
                    registered_domains,
                ),
                _registry_package_dependencies_acyclic(self.manifest.domain_packages),
            )
        )


def plan_ecosystem_domain_package_install(
    source: EcosystemRegistryManifest | EcosystemRegistryIndex,
    *,
    base_path: str | Path | None = None,
    verify: bool = True,
    trust_policy: EcosystemRegistryTrustPolicy | None = None,
    signature_verifier: EcosystemRegistrySignatureVerifier | None = None,
) -> EcosystemDomainPackageInstallPlan:
    """Validate local Domain package paths referenced by an ecosystem registry.

    The plan loads package manifests only. It does not import package entrypoints,
    activate Domain runtimes or mutate a target DomainPackageRegistry.
    """

    manifest = _registry_manifest(source)
    _verify_registry_trust(manifest, trust_policy, signature_verifier)
    if verify:
        _verify_registry_install_source(EcosystemRegistryIndex(manifest))
    candidates = tuple(
        _domain_package_install_candidate(reference, base_path=base_path)
        for reference in manifest.domain_packages
    )
    _verify_domain_package_install_dependencies(candidates)
    return EcosystemDomainPackageInstallPlan(_sort_domain_package_install_candidates(candidates))


def install_ecosystem_domain_packages(
    source: EcosystemRegistryManifest | EcosystemRegistryIndex,
    *,
    registry: DomainPackageRegistry | None = None,
    base_path: str | Path | None = None,
    verify: bool = True,
    trust_policy: EcosystemRegistryTrustPolicy | None = None,
    signature_verifier: EcosystemRegistrySignatureVerifier | None = None,
) -> EcosystemDomainPackageInstallResult:
    """Install registry-referenced Domain package metadata into a local registry."""

    plan = plan_ecosystem_domain_package_install(
        source,
        base_path=base_path,
        verify=verify,
        trust_policy=trust_policy,
        signature_verifier=signature_verifier,
    )
    target = registry or DomainPackageRegistry()
    _reject_registry_install_duplicates(plan, target)
    for package in plan.packages:
        target.register(package)
    return EcosystemDomainPackageInstallResult(target, plan.packages)


def plan_ecosystem_install(
    source: EcosystemRegistryManifest | EcosystemRegistryIndex,
    *,
    base_path: str | Path | None = None,
    verify: bool = True,
    trust_policy: EcosystemRegistryTrustPolicy | None = None,
    signature_verifier: EcosystemRegistrySignatureVerifier | None = None,
) -> EcosystemInstallPlan:
    """Validate all local artifact paths referenced by an ecosystem registry.

    The plan loads metadata manifests/configs only. It does not import Domain
    entrypoints, activate runtimes, run evaluation suites or assemble hosts.
    """

    manifest = _registry_manifest(source)
    _verify_registry_trust(manifest, trust_policy, signature_verifier)
    if verify:
        _verify_registry_install_source(EcosystemRegistryIndex(manifest))
    return EcosystemInstallPlan(
        domain_packages=plan_ecosystem_domain_package_install(
            manifest,
            base_path=base_path,
            verify=False,
            trust_policy=trust_policy,
            signature_verifier=signature_verifier,
        ),
        evaluation_datasets=tuple(
            _evaluation_dataset_install_candidate(reference, base_path=base_path)
            for reference in manifest.evaluation_datasets
        ),
        profiles=tuple(
            _profile_install_candidate(reference, base_path=base_path)
            for reference in manifest.profiles
        ),
    )


def install_ecosystem(
    source: EcosystemRegistryManifest | EcosystemRegistryIndex,
    *,
    domain_package_registry: DomainPackageRegistry | None = None,
    evaluation_dataset_registry: EvaluationDatasetRegistry | None = None,
    profile_registry: ProfileRegistry | None = None,
    base_path: str | Path | None = None,
    verify: bool = True,
    trust_policy: EcosystemRegistryTrustPolicy | None = None,
    signature_verifier: EcosystemRegistrySignatureVerifier | None = None,
) -> EcosystemInstallResult:
    """Install registry-referenced ecosystem metadata into local registries."""

    plan = plan_ecosystem_install(
        source,
        base_path=base_path,
        verify=verify,
        trust_policy=trust_policy,
        signature_verifier=signature_verifier,
    )
    domain_packages = domain_package_registry or DomainPackageRegistry()
    evaluation_datasets = evaluation_dataset_registry or EvaluationDatasetRegistry()
    _reject_registry_install_duplicates(plan.domain_packages, domain_packages)
    _reject_evaluation_dataset_install_duplicates(plan.evaluation_datasets, evaluation_datasets)
    profiles = _profile_install_registry(plan.profiles, profile_registry)

    for package in plan.domain_packages.packages:
        domain_packages.register(package)
    for dataset in (candidate.dataset for candidate in plan.evaluation_datasets):
        evaluation_datasets.register(dataset)

    return EcosystemInstallResult(
        domain_packages=domain_packages,
        evaluation_datasets=evaluation_datasets,
        profiles=profiles,
        installed_domain_packages=plan.domain_packages.packages,
        installed_evaluation_datasets=tuple(
            candidate.dataset for candidate in plan.evaluation_datasets
        ),
        installed_profiles=tuple(candidate.entry for candidate in plan.profiles),
    )


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
        resources=manifest.resources,
        dependencies=manifest.dependencies,
        compatibility=manifest.compatibility,
        security=manifest.security,
        root_path=str(package.root_path),
        manifest_path=str(package.manifest_path),
        manifest_sha256=_file_sha256(package.manifest_path),
        entrypoint=manifest.entrypoint,
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
        manifest_sha256=_file_sha256(dataset.manifest_path),
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
        config_sha256=_file_sha256(entry.path),
    )


def _registry_profile_domains_registered(
    profiles: tuple[EcosystemProfileRef, ...],
    registered_domains: frozenset[DomainIdentity],
) -> EcosystemCatalogCheck:
    missing = tuple(
        f"{profile.name}:{domain.name}@{domain.version}"
        for profile in profiles
        for domain in profile.domains
        if domain not in registered_domains
    )
    return _reference_check(
        "profile_domains_registered",
        missing,
        "all profile domains are backed by discovered Domain packages",
        "profiles reference missing Domain packages",
    )


def _registry_dataset_domains_registered(
    datasets: tuple[EcosystemEvaluationDatasetRef, ...],
    registered_domains: frozenset[DomainIdentity],
) -> EcosystemCatalogCheck:
    missing = tuple(
        f"{dataset.name}:{domain.name}@{domain.version}"
        for dataset in datasets
        for domain in dataset.domains
        if domain not in registered_domains
    )
    return _reference_check(
        "dataset_domains_registered",
        missing,
        "all evaluation dataset domains are backed by discovered Domain packages",
        "evaluation datasets reference missing Domain packages",
    )


def _registry_package_dependencies_registered(
    packages: tuple[EcosystemDomainPackageRef, ...],
    registered_domains: frozenset[DomainIdentity],
) -> EcosystemCatalogCheck:
    missing = tuple(
        f"{package.name}:{dependency.name}@{dependency.version}"
        for package in packages
        for dependency in package.dependencies
        if dependency not in registered_domains
    )
    return _reference_check(
        "package_dependencies_registered",
        missing,
        "all Domain package dependencies are present in the catalog",
        "Domain packages reference missing dependencies",
    )


def _registry_package_dependencies_acyclic(
    packages: tuple[EcosystemDomainPackageRef, ...],
) -> EcosystemCatalogCheck:
    dependency_map = {package.identity: package.dependencies for package in packages}
    cycles = _dependency_cycles(dependency_map)
    return _reference_check(
        "package_dependencies_acyclic",
        cycles,
        "Domain package dependencies are acyclic",
        "Domain package dependencies contain cycles",
    )


def _registry_manifest(
    source: EcosystemRegistryManifest | EcosystemRegistryIndex,
) -> EcosystemRegistryManifest:
    if isinstance(source, EcosystemRegistryIndex):
        return source.manifest
    return source


def _verify_registry_install_source(index: EcosystemRegistryIndex) -> None:
    report = index.verify()
    if report.passed:
        return
    failed = "; ".join(f"{check.name}: {check.message}" for check in report.failed_checks)
    raise EcosystemRegistryInstallError(
        f"ecosystem registry verification failed before package install: {failed}"
    )


def _verify_registry_trust(
    manifest: EcosystemRegistryManifest,
    policy: EcosystemRegistryTrustPolicy | None,
    signature_verifier: EcosystemRegistrySignatureVerifier | None,
) -> None:
    active = policy or EcosystemRegistryTrustPolicy()
    signed = _registry_declares_signature(manifest)
    if signed and signature_verifier is not None:
        verification = signature_verifier.verify_registry(manifest)
        if verification.passed:
            return
        raise EcosystemRegistryInstallError(
            "ecosystem registry signature verification failed"
            f" via {verification.verifier}: {verification.reason}"
        )
    if signed and not active.allow_unverified_signatures:
        raise EcosystemRegistryInstallError(
            "ecosystem registry declares signature metadata, but signature verification is not "
            "available in the local registry installer; pass a signature verifier or an explicit "
            "trust policy allowing unverified signatures only for local trusted registries"
        )
    if not signed and not active.allow_unsigned:
        raise EcosystemRegistryInstallError("unsigned ecosystem registry rejected by trust policy")


def _registry_declares_signature(manifest: EcosystemRegistryManifest) -> bool:
    metadata = manifest.metadata
    if metadata.get("signature_required") is True:
        return True
    for key in ("signature", "signatures", "signature_ref", "signed_by"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, list) and value:
            return True
    return False


def _domain_package_install_candidate(
    reference: EcosystemDomainPackageRef,
    *,
    base_path: str | Path | None,
) -> EcosystemDomainPackageInstallCandidate:
    package_path = _domain_package_ref_path(reference, base_path=base_path)
    _verify_registry_file_sha256(
        _domain_package_manifest_path(package_path),
        reference.manifest_sha256,
        label=f"domain package {_format_domain_identity(reference.identity)} manifest",
    )
    package = load_domain_package(package_path)
    if package.identity != reference.identity:
        raise EcosystemRegistryInstallError(
            "domain package identity mismatch: "
            f"registry expected {_format_domain_identity(reference.identity)}, "
            f"manifest loaded {_format_domain_identity(package.identity)}"
        )
    _verify_domain_package_ref_metadata(reference, package)
    _verify_domain_package_local_integrity(package)
    return EcosystemDomainPackageInstallCandidate(reference, package)


def _verify_domain_package_local_integrity(package: DomainPackage) -> None:
    report = verify_domain_package(package)
    if report.passed:
        return
    failed = "; ".join(f"{check.name}: {check.message}" for check in report.failed_checks)
    raise EcosystemRegistryInstallError("domain package local verification failed: " + failed)


def _domain_package_ref_path(
    reference: EcosystemDomainPackageRef,
    *,
    base_path: str | Path | None,
) -> Path:
    path_value = reference.manifest_path or reference.root_path
    if not path_value.strip():
        raise EcosystemRegistryInstallError(
            "domain package registry reference has no local path: "
            f"{_format_domain_identity(reference.identity)}"
        )
    return _resolve_registry_path(
        path_value,
        base_path=base_path,
        label=f"domain package {_format_domain_identity(reference.identity)}",
    )


def _evaluation_dataset_install_candidate(
    reference: EcosystemEvaluationDatasetRef,
    *,
    base_path: str | Path | None,
) -> EcosystemEvaluationDatasetInstallCandidate:
    dataset_path = _evaluation_dataset_ref_path(reference, base_path=base_path)
    _verify_registry_file_sha256(
        _evaluation_dataset_manifest_path(dataset_path),
        reference.manifest_sha256,
        label=f"evaluation dataset {reference.name}@{reference.version} manifest",
    )
    dataset = load_evaluation_dataset(dataset_path)
    if (dataset.identity.name, dataset.identity.version) != reference.identity:
        raise EcosystemRegistryInstallError(
            "evaluation dataset identity mismatch: "
            f"registry expected {reference.name}@{reference.version}, "
            f"manifest loaded {dataset.identity.name}@{dataset.identity.version}"
        )
    _verify_evaluation_dataset_ref_metadata(reference, dataset)
    return EcosystemEvaluationDatasetInstallCandidate(reference, dataset)


def _evaluation_dataset_ref_path(
    reference: EcosystemEvaluationDatasetRef,
    *,
    base_path: str | Path | None,
) -> Path:
    path_value = reference.manifest_path or reference.root_path
    if not path_value.strip():
        raise EcosystemRegistryInstallError(
            "evaluation dataset registry reference has no local path: "
            f"{reference.name}@{reference.version}"
        )
    return _resolve_registry_path(
        path_value,
        base_path=base_path,
        label=f"evaluation dataset {reference.name}@{reference.version}",
    )


def _profile_install_candidate(
    reference: EcosystemProfileRef,
    *,
    base_path: str | Path | None,
) -> EcosystemProfileInstallCandidate:
    profile_path = _profile_ref_path(reference, base_path=base_path)
    _verify_registry_file_sha256(
        profile_path,
        reference.config_sha256,
        label=f"profile {reference.name}@{reference.version} config",
    )
    config = ProfileConfig.from_json_file(profile_path)
    entry = ProfileCatalogEntry(config.to_profile(), config, profile_path)
    if (entry.profile.name, entry.profile.version) != reference.identity:
        raise EcosystemRegistryInstallError(
            "profile identity mismatch: "
            f"registry expected {reference.name}@{reference.version}, "
            f"config loaded {entry.profile.name}@{entry.profile.version}"
        )
    _verify_profile_ref_metadata(reference, entry)
    return EcosystemProfileInstallCandidate(reference, entry)


def _profile_ref_path(
    reference: EcosystemProfileRef,
    *,
    base_path: str | Path | None,
) -> Path:
    if not reference.path.strip():
        raise EcosystemRegistryInstallError(
            f"profile registry reference has no local path: {reference.name}@{reference.version}"
        )
    return _resolve_registry_path(
        reference.path,
        base_path=base_path,
        label=f"profile {reference.name}@{reference.version}",
    )


def _resolve_registry_path(
    path_value: str,
    *,
    base_path: str | Path | None,
    label: str,
) -> Path:
    path = Path(path_value)
    if base_path is None:
        return path
    base = Path(base_path).resolve()
    resolved = (path if path.is_absolute() else base / path).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise EcosystemRegistryInstallError(
            f"{label} path escapes registry base path: {path_value}"
        ) from exc
    return resolved


def _domain_package_manifest_path(path: Path) -> Path:
    if path.is_dir():
        return path / "manifest.json"
    return path


def _evaluation_dataset_manifest_path(path: Path) -> Path:
    if path.is_dir():
        return path / "dataset.json"
    return path


def _verify_registry_file_sha256(path: Path, expected: str, *, label: str) -> None:
    if not expected:
        return
    if not path.is_file():
        raise EcosystemRegistryInstallError(f"{label} file not found for sha256 check: {path}")
    actual = _file_sha256(path)
    if actual != expected:
        raise EcosystemRegistryInstallError(
            f"{label} sha256 mismatch: expected {expected}, got {actual}"
        )


def _verify_domain_package_ref_metadata(
    reference: EcosystemDomainPackageRef,
    package: DomainPackage,
) -> None:
    manifest = package.manifest
    mismatches: list[str] = []
    if reference.entrypoint is not None and reference.entrypoint != manifest.entrypoint:
        mismatches.append("entrypoint")
    if reference.capability_names and reference.capability_names != manifest.capabilities:
        mismatches.append("capability_names")
    if reference.required_tools and reference.required_tools != manifest.required_tools:
        mismatches.append("required_tools")
    if reference.resources and reference.resources != manifest.resources:
        mismatches.append("resources")
    if reference.dependencies and reference.dependencies != manifest.dependencies:
        mismatches.append("dependencies")
    if (
        reference.compatibility.runtime_api is not None
        and reference.compatibility.runtime_api != manifest.compatibility.runtime_api
    ):
        mismatches.append("compatibility.runtime_api")
    if (
        reference.compatibility.domain_api is not None
        and reference.compatibility.domain_api != manifest.compatibility.domain_api
    ):
        mismatches.append("compatibility.domain_api")
    if reference.security and dict(reference.security) != dict(manifest.security):
        mismatches.append("security")
    if mismatches:
        raise EcosystemRegistryInstallError(
            "domain package metadata mismatch: "
            f"{_format_domain_identity(reference.identity)} fields " + ", ".join(mismatches)
        )


def _verify_evaluation_dataset_ref_metadata(
    reference: EcosystemEvaluationDatasetRef,
    dataset: EvaluationDataset,
) -> None:
    manifest = dataset.manifest
    mismatches: list[str] = []
    if reference.description and reference.description != manifest.description:
        mismatches.append("description")
    if reference.author is not None and reference.author != manifest.author:
        mismatches.append("author")
    if reference.tags and reference.tags != manifest.tags:
        mismatches.append("tags")
    if reference.domains and reference.domains != manifest.domains:
        mismatches.append("domains")
    if reference.suites and _dataset_suite_metadata(reference.suites) != _dataset_suite_metadata(
        manifest.suites
    ):
        mismatches.append("suites")
    if mismatches:
        raise EcosystemRegistryInstallError(
            f"evaluation dataset metadata mismatch: {reference.name}@{reference.version} fields "
            + ", ".join(mismatches)
        )


def _verify_profile_ref_metadata(
    reference: EcosystemProfileRef,
    entry: ProfileCatalogEntry,
) -> None:
    mismatches: list[str] = []
    if reference.description and reference.description != entry.profile.description:
        mismatches.append("description")
    if reference.domains and reference.domains != _profile_domain_identities(entry):
        mismatches.append("domains")
    if mismatches:
        raise EcosystemRegistryInstallError(
            f"profile metadata mismatch: {reference.name}@{reference.version} fields "
            + ", ".join(mismatches)
        )


def _dataset_suite_metadata(
    suites: Iterable[_DatasetSuiteLike],
) -> tuple[tuple[str, str, str, tuple[str, ...]], ...]:
    return tuple(
        (
            suite.name,
            suite.path,
            suite.description,
            tuple(suite.tags),
        )
        for suite in suites
    )


def _verify_domain_package_install_dependencies(
    candidates: tuple[EcosystemDomainPackageInstallCandidate, ...],
) -> None:
    identities = frozenset(candidate.package.identity for candidate in candidates)
    missing = tuple(
        f"{candidate.package.identity.name}:{dependency.name}@{dependency.version}"
        for candidate in candidates
        for dependency in candidate.package.manifest.dependencies
        if dependency not in identities
    )
    if missing:
        raise EcosystemRegistryInstallError(
            "domain package install plan missing dependencies: " + ", ".join(missing)
        )


def _sort_domain_package_install_candidates(
    candidates: tuple[EcosystemDomainPackageInstallCandidate, ...],
) -> tuple[EcosystemDomainPackageInstallCandidate, ...]:
    by_identity = {candidate.package.identity: candidate for candidate in candidates}
    visiting: set[DomainIdentity] = set()
    visited: set[DomainIdentity] = set()
    stack: list[DomainIdentity] = []
    sorted_candidates: list[EcosystemDomainPackageInstallCandidate] = []

    def visit(identity: DomainIdentity) -> None:
        if identity in visited:
            return
        if identity in visiting:
            cycle = [*stack[stack.index(identity) :], identity]
            formatted = " -> ".join(_format_domain_identity(item) for item in cycle)
            raise EcosystemRegistryInstallError(
                f"domain package dependency cycle in install plan: {formatted}"
            )
        visiting.add(identity)
        stack.append(identity)
        candidate = by_identity[identity]
        for dependency in candidate.package.manifest.dependencies:
            if dependency in by_identity:
                visit(dependency)
        stack.pop()
        visiting.remove(identity)
        visited.add(identity)
        sorted_candidates.append(candidate)

    for candidate in candidates:
        visit(candidate.package.identity)
    return tuple(sorted_candidates)


def _reject_registry_install_duplicates(
    plan: EcosystemDomainPackageInstallPlan,
    registry: DomainPackageRegistry,
) -> None:
    seen: set[DomainIdentity] = set()
    duplicates: set[DomainIdentity] = set()
    for identity in plan.identities:
        if identity in seen:
            duplicates.add(identity)
        seen.add(identity)
    existing = frozenset(registry.identities())
    duplicates.update(identity for identity in plan.identities if identity in existing)
    if duplicates:
        formatted = ", ".join(sorted(_format_domain_identity(identity) for identity in duplicates))
        raise EcosystemRegistryInstallError(
            f"domain packages already registered or duplicated in install plan: {formatted}"
        )


def _reject_evaluation_dataset_install_duplicates(
    candidates: tuple[EcosystemEvaluationDatasetInstallCandidate, ...],
    registry: EvaluationDatasetRegistry,
) -> None:
    seen: set[tuple[str, str]] = set()
    duplicates: set[tuple[str, str]] = set()
    identities = tuple(
        (candidate.dataset.identity.name, candidate.dataset.identity.version)
        for candidate in candidates
    )
    for identity in identities:
        if identity in seen:
            duplicates.add(identity)
        seen.add(identity)
    existing = frozenset((identity.name, identity.version) for identity in registry.identities())
    duplicates.update(identity for identity in identities if identity in existing)
    if duplicates:
        formatted = ", ".join(f"{name}@{version}" for name, version in sorted(duplicates))
        raise EcosystemRegistryInstallError(
            f"evaluation datasets already registered or duplicated in install plan: {formatted}"
        )


def _profile_install_registry(
    candidates: tuple[EcosystemProfileInstallCandidate, ...],
    registry: ProfileRegistry | None,
) -> ProfileRegistry:
    existing_profiles = registry.profiles if registry is not None else ()
    seen: set[str] = set()
    duplicates: set[str] = set()
    names = tuple(candidate.entry.profile.name for candidate in candidates)
    for name in names:
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    existing = frozenset(profile.name for profile in existing_profiles)
    duplicates.update(name for name in names if name in existing)
    if duplicates:
        formatted = ", ".join(sorted(duplicates))
        raise EcosystemRegistryInstallError(
            f"profiles already registered or duplicated in install plan: {formatted}"
        )
    installed_profiles = tuple(candidate.entry.profile for candidate in candidates)
    return ProfileRegistry(existing_profiles + installed_profiles)


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


def _dataset_identities(
    manifest: EcosystemRegistryManifest,
) -> tuple[tuple[str, str], ...]:
    return tuple(dataset.identity for dataset in manifest.evaluation_datasets)


def _profile_identities(
    manifest: EcosystemRegistryManifest,
) -> tuple[tuple[str, str], ...]:
    return tuple(profile.identity for profile in manifest.profiles)


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
            cycles.add(" -> ".join(_format_domain_identity(item) for item in cycle))
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


def _validate_optional_sha256(field_name: str, value: str) -> None:
    if not value:
        return
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise EcosystemRegistryValidationError(f"{field_name} must be a lowercase sha256 hex")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "AmbiguousEcosystemRegistryItemError",
    "EcosystemCatalog",
    "EcosystemCatalogCheck",
    "EcosystemCatalogSummary",
    "EcosystemCatalogVerificationReport",
    "EcosystemDomainPackageInstallCandidate",
    "EcosystemDomainPackageInstallPlan",
    "EcosystemDomainPackageInstallResult",
    "EcosystemDomainPackageRef",
    "EcosystemEvaluationDatasetInstallCandidate",
    "EcosystemEvaluationDatasetRef",
    "EcosystemEvaluationDatasetSuiteRef",
    "EcosystemInstallPlan",
    "EcosystemInstallResult",
    "EcosystemProfileInstallCandidate",
    "EcosystemProfileRef",
    "EcosystemRegistryIndex",
    "EcosystemRegistryInstallError",
    "EcosystemRegistryItemNotFoundError",
    "EcosystemRegistryManifest",
    "EcosystemRegistryNotFoundError",
    "EcosystemRegistrySignatureVerification",
    "EcosystemRegistrySignatureVerifier",
    "EcosystemRegistryStoreNotFoundError",
    "EcosystemRegistryTrustPolicy",
    "EcosystemRegistryValidationError",
    "EcosystemRegistryWriteResult",
    "FileEcosystemRegistryStore",
    "build_ecosystem_registry_manifest",
    "decode_ecosystem_registry_manifest",
    "encode_ecosystem_registry_manifest",
    "install_ecosystem",
    "install_ecosystem_domain_packages",
    "load_ecosystem_catalog",
    "load_ecosystem_registry_index",
    "load_ecosystem_registry_manifest",
    "plan_ecosystem_domain_package_install",
    "plan_ecosystem_install",
    "write_ecosystem_registry_manifest",
]
