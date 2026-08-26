from __future__ import annotations

import asyncio
import json
from io import StringIO
from typing import Any, cast

from universal_agent.cli import LOCAL_PROFILE_NAME, run_cli


def read_json(raw: str) -> dict[str, Any]:
    loaded: object = json.loads(raw)
    assert isinstance(loaded, dict)
    return cast(dict[str, Any], loaded)


async def main() -> None:
    output = StringIO()
    status = await run_cli(
        [
            "kubernetes",
            "model-probe",
            LOCAL_PROFILE_NAME,
            "--workload",
            "api",
            "--namespace",
            "prod",
        ],
        stdout=output,
    )
    payload = read_json(output.getvalue())
    decision = cast(dict[str, Any], payload["decision"])

    assert status == 0
    assert payload["status"] == "ok"
    assert decision["capability"] == "inspect_workload"
    print(
        json.dumps(
            {
                "status": payload["status"],
                "model": payload["model"],
                "decision": decision,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
