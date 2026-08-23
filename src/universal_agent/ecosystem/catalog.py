from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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


__all__ = [
    "EcosystemCatalog",
    "EcosystemCatalogSummary",
    "load_ecosystem_catalog",
]
