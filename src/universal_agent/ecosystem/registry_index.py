from __future__ import annotations

from dataclasses import dataclass

from universal_agent.core import DomainIdentity
from universal_agent.ecosystem.models import (
    AmbiguousEcosystemRegistryItemError,
    EcosystemCatalogCheck,
    EcosystemCatalogSummary,
    EcosystemCatalogVerificationReport,
    EcosystemDomainPackageRef,
    EcosystemEvaluationDatasetRef,
    EcosystemProfileRef,
    EcosystemRegistryItemNotFoundError,
    EcosystemRegistryManifest,
)
from universal_agent.ecosystem.validation import (
    _ambiguous_registry_item_message,
    _dependency_cycles,
    _missing_registry_item_message,
)


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
        matches = [
            package
            for package in self.manifest.domain_packages
            if package.name == name and (version is None or package.version == version)
        ]
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
        matches = [
            dataset
            for dataset in self.manifest.evaluation_datasets
            if dataset.name == name and (version is None or dataset.version == version)
        ]
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
        matches = [
            profile
            for profile in self.manifest.profiles
            if profile.name == name and (version is None or profile.version == version)
        ]
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


def _reference_check(
    name: str,
    missing: tuple[str, ...],
    passed_message: str,
    failed_prefix: str,
) -> EcosystemCatalogCheck:
    if not missing:
        return EcosystemCatalogCheck(name, True, passed_message)
    return EcosystemCatalogCheck(name, False, f"{failed_prefix}: {', '.join(missing)}")
