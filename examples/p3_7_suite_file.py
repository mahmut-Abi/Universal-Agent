from __future__ import annotations

import asyncio
import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from universal_agent.cli import LOCAL_PROFILE_NAME, run_cli


def read_json(buffer: StringIO) -> dict[str, Any]:
    loaded: object = json.loads(buffer.getvalue())
    assert isinstance(loaded, dict)
    return loaded


def write_suite(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "name": "external evaluation suite",
                "tags": ["file", "kubernetes"],
                "quality_gate": {"min_action_success_rate": 1.0},
                "scenarios": [
                    {
                        "name": "external healthy workload",
                        "kind": "regression",
                        "tags": ["smoke", "file"],
                        "goal": {
                            "description": "Evaluate workload health from an external suite",
                            "success_criteria": {"healthy": True},
                        },
                        "task": {
                            "description": "Inspect workload from external suite",
                            "required_criteria": ["healthy"],
                        },
                        "expectations": {
                            "expected_status": "completed",
                            "expected_criteria": {"healthy": True},
                            "required_events": ["GoalCompleted", "EvaluationCompleted"],
                            "required_evidence_claims": ["healthy"],
                            "required_capabilities": ["inspect_workload"],
                            "allowed_capabilities": ["inspect_workload"],
                            "max_actions": 1,
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


async def main() -> None:
    with TemporaryDirectory() as directory:
        suite_path = Path(directory) / "suite.json"
        write_suite(suite_path)
        output = StringIO()
        status = await run_cli(
            [
                "eval",
                "run",
                LOCAL_PROFILE_NAME,
                "--suite-file",
                str(suite_path),
                "--fail-on-fail",
            ],
            stdout=output,
        )
        payload = read_json(output)

    print(f"status={status}")
    print(f"suite={payload['suite']['suite_name']}")
    print(f"passed={payload['passed']}")


if __name__ == "__main__":
    asyncio.run(main())
