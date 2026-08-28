from __future__ import annotations

from collections.abc import Iterable
from graphlib import CycleError, TopologicalSorter
from typing import Any, Protocol, cast

from universal_agent.core import DomainIdentity
from universal_agent.core.config_validation import (
    duplicate_values,
    parse_non_empty_string,
    parse_non_empty_string_sequence,
    parse_optional_lower_sha256_hex_digest,
)
from universal_agent.domain import DomainPackageCompatibility


class _HasEvaluationDatasets(Protocol):
    evaluation_datasets: Iterable[Any]


class _HasProfiles(Protocol):
    profiles: Iterable[Any]


def _identity_body(identity: DomainIdentity) -> dict[str, str]:
    return {"name": identity.name, "version": identity.version}


def _compatibility_body(compatibility: DomainPackageCompatibility) -> dict[str, str]:
    body: dict[str, str] = {}
    if compatibility.runtime_api is not None:
        body["runtime_api"] = compatibility.runtime_api
    if compatibility.domain_api is not None:
        body["domain_api"] = compatibility.domain_api
    return body


def _dependency_cycles(
    dependencies: dict[DomainIdentity, tuple[DomainIdentity, ...]],
) -> tuple[str, ...]:
    known = frozenset(dependencies)
    sorter: TopologicalSorter[DomainIdentity] = TopologicalSorter()
    for identity in sorted(dependencies, key=lambda item: (item.name, item.version)):
        sorter.add(
            identity,
            *(dependency for dependency in dependencies[identity] if dependency in known),
        )
    try:
        tuple(sorter.static_order())
    except CycleError as exc:
        cycle = cast(list[DomainIdentity], exc.args[1])
        return (" -> ".join(_format_domain_identity(item) for item in cycle),)
    return ()


def _reject_duplicates(label: str, identities: tuple[str, ...]) -> None:
    duplicates = duplicate_values(identities)
    if duplicates:
        from universal_agent.ecosystem.models import EcosystemRegistryValidationError

        raise EcosystemRegistryValidationError(
            f"duplicate {label} references: {', '.join(duplicates)}"
        )


def _missing_registry_item_message(label: str, name: str, version: str | None) -> str:
    if version is None:
        return f"{label} not found in ecosystem registry: {name}"
    return f"{label} not found in ecosystem registry: {name}@{version}"


def _ambiguous_registry_item_message(
    label: str,
    name: str,
    matches: Iterable[Any],
) -> str:
    versions = ", ".join(sorted(str(getattr(item, "version", "")) for item in matches))
    return f"{label} {name} has multiple versions in ecosystem registry: {versions}"


def _format_domain_identity(identity: DomainIdentity) -> str:
    return f"{identity.name}@{identity.version}"


def _require_non_empty(value: str, field_name: str) -> None:
    try:
        parse_non_empty_string(value, field_name)
    except ValueError as exc:
        from universal_agent.ecosystem.models import EcosystemRegistryValidationError

        raise EcosystemRegistryValidationError(str(exc)) from exc


def _validate_strings(field_name: str, values: tuple[str, ...]) -> None:
    try:
        parse_non_empty_string_sequence(values, field_name)
    except ValueError as exc:
        from universal_agent.ecosystem.models import EcosystemRegistryValidationError

        raise EcosystemRegistryValidationError(str(exc)) from exc


def _validate_optional_sha256(field_name: str, value: str) -> None:
    try:
        parse_optional_lower_sha256_hex_digest(value, field_name)
    except ValueError as exc:
        from universal_agent.ecosystem.models import EcosystemRegistryValidationError

        raise EcosystemRegistryValidationError(str(exc)) from exc


def _dataset_identities(manifest: object) -> tuple[tuple[str, str], ...]:
    datasets = cast(_HasEvaluationDatasets, manifest).evaluation_datasets
    return tuple((str(dataset.name), str(dataset.version)) for dataset in datasets)


def _profile_identities(manifest: object) -> tuple[tuple[str, str], ...]:
    profiles = cast(_HasProfiles, manifest).profiles
    return tuple((str(profile.name), str(profile.version)) for profile in profiles)
