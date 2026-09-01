from __future__ import annotations

import asyncio
import json
from io import StringIO
from typing import Any

from universal_agent_cli import LOCAL_PROFILE_NAME, run_cli


def read_json(buffer: StringIO) -> dict[str, Any]:
    loaded: object = json.loads(buffer.getvalue())
    assert isinstance(loaded, dict)
    return loaded


async def main() -> None:
    output = StringIO()
    status = await run_cli(
        [
            "eval",
            "run",
            LOCAL_PROFILE_NAME,
            "--min-goal-completion-rate",
            "0.5",
            "--min-action-success-rate",
            "1.0",
            "--max-tool-failure-rate",
            "0.0",
            "--max-policy-denial-rate",
            "0.5",
            "--max-average-recoveries",
            "0.0",
            "--max-average-actions",
            "0.5",
            "--max-average-model-calls",
            "0.0",
            "--max-average-model-tokens",
            "0.0",
            "--max-total-model-cost-micros",
            "0",
            "--fail-on-fail",
        ],
        stdout=output,
    )
    payload = read_json(output)
    gate = payload["gate"]
    assert isinstance(gate, dict)
    checks = gate["checks"]
    assert isinstance(checks, list)

    print(f"status={status}")
    print(f"passed={payload['passed']}")
    print(f"gate_checks={len(checks)}")


if __name__ == "__main__":
    asyncio.run(main())
