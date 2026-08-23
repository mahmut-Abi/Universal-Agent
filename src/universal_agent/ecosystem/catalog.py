from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from universal_agent.core import DomainIdentity
from universal_agent.domain import DomainPackage, DomainPackageRegistry
from universal_agent.evaluation.dataset import EvaluationDataset, EvaluationDatasetRegistry
from universal_agent.profile import ProfileCatalog, ProfileCatalogEntry


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


__all__ = [
    "EcosystemCatalog",
    "EcosystemCatalogCheck",
    "EcosystemCatalogSummary",
    "EcosystemCatalogVerificationReport",
    "load_ecosystem_catalog",
]
