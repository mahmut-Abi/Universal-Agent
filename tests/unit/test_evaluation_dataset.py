from __future__ import annotations

import json
from pathlib import Path

import pytest

from universal_agent.core import DomainIdentity, JsonMapping, JsonValue
from universal_agent.evaluation.dataset import (
    AmbiguousEvaluationDatasetError,
    EvaluationDatasetIdentity,
    EvaluationDatasetNotFoundError,
    EvaluationDatasetRegistry,
    EvaluationDatasetValidationError,
    decode_evaluation_dataset_manifest,
    encode_evaluation_dataset_manifest,
    load_evaluation_dataset,
    verify_evaluation_dataset,
)


def suite_payload(name: str = "healthy workload") -> dict[str, JsonValue]:
    return {
        "name": name,
        "scenarios": [
            {
                "name": "healthy workload",
                "kind": "regression",
                "goal": {
                    "description": "Evaluate workload health",
                    "success_criteria": {"healthy": True},
                },
                "task": {
                    "description": "Inspect workload",
                    "required_criteria": ["healthy"],
                },
            }
        ],
    }


def dataset_payload(
    name: str = "kubernetes-remediation",
    version: str = "1.0.0",
    *,
    suite_path: str = "suites/healthy.json",
    tags: tuple[str, ...] = ("kubernetes", "regression"),
) -> dict[str, JsonValue]:
    return {
        "apiVersion": "agent.nantian.dev/v1alpha1",
        "kind": "EvaluationDataset",
        "metadata": {
            "name": name,
            "version": version,
            "description": f"{name} evaluation dataset",
            "author": "Runtime Team",
            "tags": list(tags),
        },
        "domains": [{"name": "kubernetes", "version": "0.2.0"}],
        "suites": [
            {
                "name": "healthy",
                "path": suite_path,
                "description": "Healthy workload regression suite",
                "tags": ["smoke"],
            }
        ],
    }


def write_json(path: Path, payload: JsonMapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_dataset(root: Path, payload: JsonMapping) -> Path:
    write_json(root / "suites" / "healthy.json", suite_payload())
    manifest_path = root / "dataset.json"
    write_json(manifest_path, payload)
    return manifest_path


def test_decode_evaluation_dataset_manifest_round_trips_catalog_metadata() -> None:
    manifest = decode_evaluation_dataset_manifest(dataset_payload())
    encoded = encode_evaluation_dataset_manifest(manifest)
    decoded = decode_evaluation_dataset_manifest(encoded)

    assert decoded.identity == EvaluationDatasetIdentity("kubernetes-remediation", "1.0.0")
    assert decoded.author == "Runtime Team"
    assert decoded.domains == (DomainIdentity("kubernetes", "0.2.0"),)
    assert decoded.tags == ("kubernetes", "regression")
    assert decoded.suites[0].name == "healthy"
    assert decoded.suites[0].path == "suites/healthy.json"


def test_load_evaluation_dataset_validates_referenced_suite_file(tmp_path: Path) -> None:
    manifest_path = write_dataset(tmp_path / "kubernetes-dataset", dataset_payload())

    dataset = load_evaluation_dataset(manifest_path)

    assert dataset.identity == EvaluationDatasetIdentity("kubernetes-remediation", "1.0.0")
    assert dataset.manifest_path == manifest_path
    assert dataset.suite_path(dataset.manifest.suites[0]).name == "healthy.json"


def test_evaluation_dataset_registry_discovers_and_filters_datasets(tmp_path: Path) -> None:
    write_dataset(tmp_path / "beta-dataset", dataset_payload("beta", tags=("database",)))
    write_dataset(tmp_path / "alpha-dataset", dataset_payload("alpha", tags=("kubernetes",)))

    registry = EvaluationDatasetRegistry()
    datasets = registry.discover(tmp_path)

    assert [dataset.identity.name for dataset in datasets] == ["alpha", "beta"]
    assert registry.identities() == (
        EvaluationDatasetIdentity("alpha", "1.0.0"),
        EvaluationDatasetIdentity("beta", "1.0.0"),
    )
    assert [dataset.identity.name for dataset in registry.list(tag="kubernetes")] == ["alpha"]
    assert [
        dataset.identity.name
        for dataset in registry.list(domain=DomainIdentity("kubernetes", "0.2.0"))
    ] == ["alpha", "beta"]


def test_evaluation_dataset_registry_reports_duplicate_missing_and_ambiguous_datasets(
    tmp_path: Path,
) -> None:
    registry = EvaluationDatasetRegistry()
    dataset = registry.install(write_dataset(tmp_path / "alpha-v1", dataset_payload("alpha")))

    with pytest.raises(EvaluationDatasetValidationError, match="already registered"):
        registry.register(dataset)

    with pytest.raises(EvaluationDatasetNotFoundError, match="beta"):
        registry.get_by_name("beta")

    registry.install(write_dataset(tmp_path / "alpha-v2", dataset_payload("alpha", "2.0.0")))

    with pytest.raises(AmbiguousEvaluationDatasetError, match="multiple registered versions"):
        registry.get_by_name("alpha")


def test_load_evaluation_dataset_rejects_missing_suite_and_unsafe_paths(tmp_path: Path) -> None:
    root = tmp_path / "broken-dataset"
    root.mkdir()
    write_json(root / "dataset.json", dataset_payload(suite_path="suites/missing.json"))

    with pytest.raises(EvaluationDatasetNotFoundError, match="suite file not found"):
        load_evaluation_dataset(root)

    with pytest.raises(EvaluationDatasetValidationError, match="relative package path"):
        decode_evaluation_dataset_manifest(dataset_payload(suite_path="../suite.json"))


def test_evaluation_dataset_verification_checks_local_manifest_and_suites(
    tmp_path: Path,
) -> None:
    root = tmp_path / "kubernetes-dataset"
    write_dataset(root, dataset_payload())
    dataset = load_evaluation_dataset(root)

    passing = verify_evaluation_dataset(dataset)
    (root / "suites" / "healthy.json").unlink()
    failing = verify_evaluation_dataset(dataset)
    failed_checks = {check.name: check.message for check in failing.failed_checks}

    assert passing.passed is True
    assert {check.name for check in passing.checks} == {
        "dataset_root_exists",
        "dataset_manifest_exists",
        "dataset_manifest_matches_identity",
        "dataset_suites_load",
    }
    assert failing.passed is False
    assert "dataset_suites_load" in failed_checks
    assert "suite file not found" in failed_checks["dataset_suites_load"]


def test_evaluation_dataset_registry_verification_checks_all_registered_datasets(
    tmp_path: Path,
) -> None:
    write_dataset(tmp_path / "alpha-dataset", dataset_payload("alpha"))
    write_dataset(tmp_path / "beta-dataset", dataset_payload("beta"))
    registry = EvaluationDatasetRegistry()
    registry.discover(tmp_path)

    passing = registry.verify()
    (tmp_path / "beta-dataset" / "dataset.json").write_text(
        json.dumps(dataset_payload("renamed"), indent=2),
        encoding="utf-8",
    )
    failing = registry.verify()
    failed_checks = {check.name: check.message for check in failing.failed_checks}

    assert passing.passed is True
    assert failing.passed is False
    assert "dataset_manifest_matches_identity" in failed_checks
    assert "identity mismatch" in failed_checks["dataset_manifest_matches_identity"]
