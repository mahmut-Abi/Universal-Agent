from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import Field

from universal_agent.core import DomainIdentity, JsonMapping, immutable_json
from universal_agent.core.config_validation import (
    ConfigPayload,
    PydanticJsonValue,
    json_mapping,
    parse_json_object,
    parse_payload,
)
from universal_agent.evaluation.scenario_config import load_evaluation_suite_config

EVALUATION_DATASET_MANIFEST = "dataset.json"


class EvaluationDatasetValidationError(ValueError):
    pass


class EvaluationDatasetNotFoundError(LookupError):
    pass


class AmbiguousEvaluationDatasetError(ValueError):
    pass


class _EvaluationDatasetMetadataPayload(ConfigPayload):
    name: str
    version: str
    description: str
    author: str | None = None
    tags: list[str] = Field(default_factory=list)


class _EvaluationDatasetDomainPayload(ConfigPayload):
    name: str
    version: str


class _EvaluationDatasetSuitePayload(ConfigPayload):
    name: str
    path: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


class _EvaluationDatasetManifestPayload(ConfigPayload):
    api_version_camel: str | None = Field(default=None, alias="apiVersion")
    api_version: str | None = None
    kind: str
    metadata: dict[str, PydanticJsonValue]
    domains: list[_EvaluationDatasetDomainPayload] = Field(default_factory=list)
    suites: list[_EvaluationDatasetSuitePayload]


@dataclass(frozen=True, slots=True)
class EvaluationDatasetIdentity:
    name: str
    version: str

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "name")
        _require_non_empty(self.version, "version")


@dataclass(frozen=True, slots=True)
class EvaluationDatasetSuiteRef:
    name: str
    path: str
    description: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "suite.name")
        _require_non_empty(self.path, "suite.path")
        _validate_relative_path(self.path, "suite.path")
        _validate_strings("suite.tags", self.tags)


@dataclass(frozen=True, slots=True)
class EvaluationDatasetManifest:
    api_version: str
    kind: str
    name: str
    version: str
    description: str
    suites: tuple[EvaluationDatasetSuiteRef, ...]
    author: str | None = None
    domains: tuple[DomainIdentity, ...] = ()
    tags: tuple[str, ...] = ()
    metadata: JsonMapping = field(default_factory=immutable_json)

    def __post_init__(self) -> None:
        _require_non_empty(self.api_version, "api_version")
        _require_non_empty(self.kind, "kind")
        _require_non_empty(self.name, "metadata.name")
        _require_non_empty(self.version, "metadata.version")
        _require_non_empty(self.description, "metadata.description")
        if self.kind != "EvaluationDataset":
            raise EvaluationDatasetValidationError("kind must be EvaluationDataset")
        if self.author is not None:
            _require_non_empty(self.author, "metadata.author")
        if not self.suites:
            raise EvaluationDatasetValidationError(
                "evaluation dataset must include at least one suite"
            )
        duplicates = _duplicates(tuple(suite.name for suite in self.suites))
        if duplicates:
            raise EvaluationDatasetValidationError(
                "duplicate evaluation dataset suites: " + ", ".join(duplicates)
            )
        _validate_strings("metadata.tags", self.tags)
        for index, domain in enumerate(self.domains):
            _require_non_empty(domain.name, f"domains[{index}].name")
            _require_non_empty(domain.version, f"domains[{index}].version")

    @property
    def identity(self) -> EvaluationDatasetIdentity:
        return EvaluationDatasetIdentity(self.name, self.version)


@dataclass(frozen=True, slots=True)
class EvaluationDataset:
    manifest: EvaluationDatasetManifest
    root_path: Path
    manifest_path: Path

    @property
    def identity(self) -> EvaluationDatasetIdentity:
        return self.manifest.identity

    def suite_path(self, suite: EvaluationDatasetSuiteRef) -> Path:
        return self.root_path / suite.path


@dataclass(frozen=True, slots=True)
class EvaluationDatasetCheck:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class EvaluationDatasetVerificationReport:
    checks: tuple[EvaluationDatasetCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed_checks(self) -> tuple[EvaluationDatasetCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)


class EvaluationDatasetRegistry:
    """P7 catalog for reusable evaluation suite datasets.

    The registry validates and stores dataset metadata plus suite references.
    It does not run scenarios or inspect Runtime internals.
    """

    def __init__(self, datasets: tuple[EvaluationDataset, ...] = ()) -> None:
        self._datasets: dict[EvaluationDatasetIdentity, EvaluationDataset] = {}
        self._order: list[EvaluationDatasetIdentity] = []
        for dataset in datasets:
            self.register(dataset)

    def register(self, dataset: EvaluationDataset) -> EvaluationDataset:
        identity = dataset.identity
        if identity in self._datasets:
            raise EvaluationDatasetValidationError(
                f"evaluation dataset already registered: {_format_identity(identity)}"
            )
        self._datasets[identity] = dataset
        self._order.append(identity)
        return dataset

    def discover(self, root: Path) -> tuple[EvaluationDataset, ...]:
        datasets = tuple(load_evaluation_dataset(path) for path in _manifest_paths(root))
        for dataset in datasets:
            self.register(dataset)
        return datasets

    def install(self, path: Path) -> EvaluationDataset:
        return self.register(load_evaluation_dataset(path))

    def list(
        self,
        *,
        tag: str | None = None,
        domain: DomainIdentity | None = None,
    ) -> tuple[EvaluationDataset, ...]:
        datasets = tuple(self._datasets[identity] for identity in self._order)
        if tag is not None:
            datasets = tuple(dataset for dataset in datasets if tag in dataset.manifest.tags)
        if domain is not None:
            datasets = tuple(dataset for dataset in datasets if domain in dataset.manifest.domains)
        return datasets

    def identities(self) -> tuple[EvaluationDatasetIdentity, ...]:
        return tuple(dataset.identity for dataset in self.list())

    def get(self, identity: EvaluationDatasetIdentity) -> EvaluationDataset:
        try:
            return self._datasets[identity]
        except KeyError as exc:
            raise EvaluationDatasetNotFoundError(
                f"evaluation dataset not registered: {_format_identity(identity)}"
            ) from exc

    def get_by_name(self, name: str) -> EvaluationDataset:
        matches = tuple(dataset for dataset in self.list() if dataset.identity.name == name)
        if not matches:
            raise EvaluationDatasetNotFoundError(f"evaluation dataset not registered: {name}")
        if len(matches) > 1:
            versions = ", ".join(sorted(dataset.identity.version for dataset in matches))
            raise AmbiguousEvaluationDatasetError(
                f"evaluation dataset {name} has multiple registered versions: {versions}"
            )
        return matches[0]

    def verify(self) -> EvaluationDatasetVerificationReport:
        return verify_evaluation_dataset_registry(self)


def load_evaluation_dataset(path: str | Path) -> EvaluationDataset:
    manifest_path = _manifest_path(Path(path))
    payload = _load_json_object(manifest_path)
    manifest = decode_evaluation_dataset_manifest(payload)
    dataset = EvaluationDataset(
        manifest=manifest,
        root_path=manifest_path.parent,
        manifest_path=manifest_path,
    )
    for suite in manifest.suites:
        _validate_suite_file(dataset, suite)
    return dataset


def verify_evaluation_dataset(dataset: EvaluationDataset) -> EvaluationDatasetVerificationReport:
    return EvaluationDatasetVerificationReport(
        (
            _dataset_root_exists(dataset),
            _dataset_manifest_exists(dataset),
            _dataset_manifest_matches_identity(dataset),
            _dataset_suites_load(dataset),
        )
    )


def verify_evaluation_dataset_registry(
    registry: EvaluationDatasetRegistry,
) -> EvaluationDatasetVerificationReport:
    checks = tuple(
        check for dataset in registry.list() for check in verify_evaluation_dataset(dataset).checks
    )
    return EvaluationDatasetVerificationReport(checks)


def decode_evaluation_dataset_manifest(payload: JsonMapping) -> EvaluationDatasetManifest:
    manifest_payload = _parse_dataset_payload(_EvaluationDatasetManifestPayload, payload)
    metadata_payload = _parse_dataset_payload(
        _EvaluationDatasetMetadataPayload,
        json_mapping(manifest_payload.metadata),
    )
    return EvaluationDatasetManifest(
        api_version=_api_version(manifest_payload),
        kind=manifest_payload.kind,
        name=metadata_payload.name,
        version=metadata_payload.version,
        description=metadata_payload.description,
        author=metadata_payload.author,
        suites=tuple(
            EvaluationDatasetSuiteRef(
                name=suite.name,
                path=suite.path,
                description=suite.description or "",
                tags=_string_tuple(suite.tags, "tags"),
            )
            for suite in manifest_payload.suites
        ),
        domains=tuple(
            DomainIdentity(domain.name, domain.version) for domain in manifest_payload.domains
        ),
        tags=_string_tuple(metadata_payload.tags, "tags"),
        metadata=immutable_json(json_mapping(manifest_payload.metadata)),
    )


def encode_evaluation_dataset_manifest(manifest: EvaluationDatasetManifest) -> dict[str, Any]:
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
    return {
        "apiVersion": manifest.api_version,
        "kind": manifest.kind,
        "metadata": metadata,
        "domains": [
            {"name": domain.name, "version": domain.version} for domain in manifest.domains
        ],
        "suites": [
            {
                "name": suite.name,
                "path": suite.path,
                "description": suite.description,
                "tags": list(suite.tags),
            }
            for suite in manifest.suites
        ],
    }


def _validate_suite_file(dataset: EvaluationDataset, suite: EvaluationDatasetSuiteRef) -> None:
    path = dataset.suite_path(suite)
    if not path.exists():
        raise EvaluationDatasetNotFoundError(f"evaluation suite file not found: {path}")
    try:
        load_evaluation_suite_config(path)
    except ValueError as exc:
        raise EvaluationDatasetValidationError(
            f"invalid evaluation suite file referenced by dataset {dataset.identity.name}: {path}"
        ) from exc


def _dataset_root_exists(dataset: EvaluationDataset) -> EvaluationDatasetCheck:
    if dataset.root_path.is_dir():
        return EvaluationDatasetCheck(
            "dataset_root_exists",
            True,
            f"evaluation dataset root exists: {_format_identity(dataset.identity)}",
        )
    return EvaluationDatasetCheck(
        "dataset_root_exists",
        False,
        f"evaluation dataset root missing or not a directory: {dataset.root_path}",
    )


def _dataset_manifest_exists(dataset: EvaluationDataset) -> EvaluationDatasetCheck:
    if dataset.manifest_path.is_file():
        return EvaluationDatasetCheck(
            "dataset_manifest_exists",
            True,
            f"evaluation dataset manifest exists: {_format_identity(dataset.identity)}",
        )
    return EvaluationDatasetCheck(
        "dataset_manifest_exists",
        False,
        f"evaluation dataset manifest missing or not a file: {dataset.manifest_path}",
    )


def _dataset_manifest_matches_identity(dataset: EvaluationDataset) -> EvaluationDatasetCheck:
    try:
        loaded = decode_evaluation_dataset_manifest(_load_json_object(dataset.manifest_path))
    except (EvaluationDatasetNotFoundError, EvaluationDatasetValidationError) as exc:
        return EvaluationDatasetCheck(
            "dataset_manifest_matches_identity",
            False,
            f"evaluation dataset manifest could not be loaded: {exc}",
        )
    loaded_identity = loaded.identity
    if loaded_identity == dataset.identity:
        return EvaluationDatasetCheck(
            "dataset_manifest_matches_identity",
            True,
            f"evaluation dataset manifest identity matches: {_format_identity(dataset.identity)}",
        )
    return EvaluationDatasetCheck(
        "dataset_manifest_matches_identity",
        False,
        "evaluation dataset identity mismatch: "
        f"expected {_format_identity(dataset.identity)}, "
        f"loaded {_format_identity(loaded_identity)}",
    )


def _dataset_suites_load(dataset: EvaluationDataset) -> EvaluationDatasetCheck:
    try:
        load_evaluation_dataset(dataset.manifest_path)
    except (EvaluationDatasetNotFoundError, EvaluationDatasetValidationError) as exc:
        return EvaluationDatasetCheck(
            "dataset_suites_load",
            False,
            f"evaluation dataset suites could not be loaded: {exc}",
        )
    return EvaluationDatasetCheck(
        "dataset_suites_load",
        True,
        f"all evaluation dataset suites load: {_format_identity(dataset.identity)}",
    )


def _manifest_paths(root: Path) -> tuple[Path, ...]:
    if root.is_file():
        return (_manifest_path(root),)
    if not root.exists():
        raise EvaluationDatasetNotFoundError(f"evaluation dataset root not found: {root}")
    return tuple(sorted(root.rglob(EVALUATION_DATASET_MANIFEST)))


def _manifest_path(path: Path) -> Path:
    if path.is_dir():
        return path / EVALUATION_DATASET_MANIFEST
    return path


def _load_json_object(path: Path) -> JsonMapping:
    if not path.exists():
        raise EvaluationDatasetNotFoundError(f"evaluation dataset manifest not found: {path}")
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvaluationDatasetValidationError(
            f"invalid evaluation dataset manifest JSON: {path}"
        ) from exc
    if not isinstance(loaded, dict):
        raise EvaluationDatasetValidationError("evaluation dataset manifest must be a JSON object")
    try:
        return parse_json_object(loaded, "evaluation dataset manifest")
    except ValueError as exc:
        raise EvaluationDatasetValidationError(str(exc)) from exc


def _api_version(payload: _EvaluationDatasetManifestPayload) -> str:
    camel = payload.api_version_camel
    snake = payload.api_version
    if camel is not None and snake is not None and camel != snake:
        raise EvaluationDatasetValidationError("apiVersion and api_version must match")
    value = camel if camel is not None else snake
    if value is None:
        raise EvaluationDatasetValidationError("apiVersion must be a string")
    _require_non_empty(value, "apiVersion")
    return value


def _string_tuple(value: Sequence[str], key: str) -> tuple[str, ...]:
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise EvaluationDatasetValidationError(f"{key}[{index}] must be a non-empty string")
        items.append(item)
    return tuple(items)


def _parse_dataset_payload[T: ConfigPayload](
    model_type: type[T],
    payload: JsonMapping,
) -> T:
    try:
        return parse_payload(model_type, payload)
    except ValueError as exc:
        raise EvaluationDatasetValidationError(str(exc)) from exc


def _validate_strings(field_name: str, values: Sequence[str]) -> None:
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            raise EvaluationDatasetValidationError(
                f"{field_name}[{index}] must be a non-empty string"
            )


def _validate_relative_path(value: str, field_name: str) -> None:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise EvaluationDatasetValidationError(f"{field_name} must be a relative package path")


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise EvaluationDatasetValidationError(f"{field_name} must not be empty")


def _duplicates(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return tuple(sorted(duplicates))


def _format_identity(identity: EvaluationDatasetIdentity) -> str:
    return f"{identity.name}@{identity.version}"


__all__ = [
    "EVALUATION_DATASET_MANIFEST",
    "AmbiguousEvaluationDatasetError",
    "EvaluationDataset",
    "EvaluationDatasetCheck",
    "EvaluationDatasetIdentity",
    "EvaluationDatasetManifest",
    "EvaluationDatasetNotFoundError",
    "EvaluationDatasetRegistry",
    "EvaluationDatasetSuiteRef",
    "EvaluationDatasetValidationError",
    "EvaluationDatasetVerificationReport",
    "decode_evaluation_dataset_manifest",
    "encode_evaluation_dataset_manifest",
    "load_evaluation_dataset",
    "verify_evaluation_dataset",
    "verify_evaluation_dataset_registry",
]
