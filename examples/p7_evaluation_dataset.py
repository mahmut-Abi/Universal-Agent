from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import EvaluationDatasetRegistry


def write_suite(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "name": "kubernetes remediation",
                "tags": ["kubernetes", "regression"],
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
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def write_dataset(root: Path) -> None:
    write_suite(root / "suites" / "healthy.json")
    (root / "dataset.json").write_text(
        json.dumps(
            {
                "apiVersion": "agent.nantian.dev/v1alpha1",
                "kind": "EvaluationDataset",
                "metadata": {
                    "name": "kubernetes-remediation",
                    "version": "1.0.0",
                    "description": "Kubernetes remediation evaluation dataset",
                    "author": "Runtime Team",
                    "tags": ["kubernetes", "regression"],
                },
                "domains": [{"name": "kubernetes", "version": "0.2.0"}],
                "suites": [
                    {
                        "name": "healthy",
                        "path": "suites/healthy.json",
                        "description": "Healthy workload regression suite",
                        "tags": ["smoke"],
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory) / "kubernetes-dataset"
        root.mkdir()
        write_dataset(root)

        registry = EvaluationDatasetRegistry()
        registry.discover(Path(directory))
        dataset = registry.get_by_name("kubernetes-remediation")

        print(f"datasets={','.join(identity.name for identity in registry.identities())}")
        print(f"suite_count={len(dataset.manifest.suites)}")
        print(f"suite_path={dataset.suite_path(dataset.manifest.suites[0]).name}")


if __name__ == "__main__":
    main()
