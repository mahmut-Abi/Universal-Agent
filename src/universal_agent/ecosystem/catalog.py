from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from universal_agent.core import DomainIdentity, JsonMapping, immutable_json
from universal_agent.core.config_validation import duplicate_values
from universal_agent.domain import (
    DomainPackage,
    DomainPackageRegistry,
    load_domain_package,
    verify_domain_package,
)
from universal_agent.ecosystem.models import (
    ECOSYSTEM_REGISTRY_KIND,
    AmbiguousEcosystemRegistryItemError,
    EcosystemCatalogCheck,
    EcosystemCatalogSummary,
    EcosystemCatalogVerificationReport,
    EcosystemDomainPackageInstallCandidate,
    EcosystemDomainPackageInstallPlan,
    EcosystemDomainPackageInstallResult,
    EcosystemDomainPackageRef,
    EcosystemEvaluationDatasetInstallCandidate,
    EcosystemEvaluationDatasetRef,
    EcosystemEvaluationDatasetSuiteRef,
    EcosystemInstallPlan,
    EcosystemInstallResult,
    EcosystemProfileInstallCandidate,
    EcosystemProfileRef,
    EcosystemRegistryInstallError,
    EcosystemRegistryItemNotFoundError,
    EcosystemRegistryManifest,
    EcosystemRegistryNotFoundError,
    EcosystemRegistrySignatureVerification,
    EcosystemRegistrySignatureVerifier,
    EcosystemRegistryStoreNotFoundError,
    EcosystemRegistryTrustPolicy,
    EcosystemRegistryValidationError,
    EcosystemRegistryWriteResult,
    _DatasetSuiteLike,
)
from universal_agent.ecosystem.registry_codec import (
    decode_ecosystem_registry_manifest,
    encode_ecosystem_registry_manifest,
    load_ecosystem_registry_index,
    load_ecosystem_registry_manifest,
    write_ecosystem_registry_manifest,
)
from universal_agent.ecosystem.registry_index import EcosystemRegistryIndex
from universal_agent.ecosystem.registry_store import FileEcosystemRegistryStore
from universal_agent.ecosystem.validation import _format_domain_identity
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
    identity_names = tuple(_format_domain_identity(identity) for identity in plan.identities)
    duplicates = set(duplicate_values(identity_names))
    existing = frozenset(registry.identities())
    duplicates.update(
        _format_domain_identity(identity) for identity in plan.identities if identity in existing
    )
    if duplicates:
        formatted = ", ".join(sorted(duplicates))
        raise EcosystemRegistryInstallError(
            f"domain packages already registered or duplicated in install plan: {formatted}"
        )


def _reject_evaluation_dataset_install_duplicates(
    candidates: tuple[EcosystemEvaluationDatasetInstallCandidate, ...],
    registry: EvaluationDatasetRegistry,
) -> None:
    identities = tuple(
        f"{candidate.dataset.identity.name}@{candidate.dataset.identity.version}"
        for candidate in candidates
    )
    duplicates = set(duplicate_values(identities))
    existing = frozenset(
        f"{identity.name}@{identity.version}" for identity in registry.identities()
    )
    duplicates.update(identity for identity in identities if identity in existing)
    if duplicates:
        formatted = ", ".join(sorted(duplicates))
        raise EcosystemRegistryInstallError(
            f"evaluation datasets already registered or duplicated in install plan: {formatted}"
        )


def _profile_install_registry(
    candidates: tuple[EcosystemProfileInstallCandidate, ...],
    registry: ProfileRegistry | None,
) -> ProfileRegistry:
    existing_profiles = registry.profiles if registry is not None else ()
    names = tuple(candidate.entry.profile.name for candidate in candidates)
    duplicates = set(duplicate_values(names))
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
