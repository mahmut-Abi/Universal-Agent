from __future__ import annotations

import asyncio
import json
from io import StringIO
from typing import Any, cast

from universal_agent_cli import LOCAL_PROFILE_NAME, run_cli


def read_json(raw: str) -> dict[str, Any]:
    loaded: object = json.loads(raw)
    assert isinstance(loaded, dict)
    return cast(dict[str, Any], loaded)


async def main() -> None:
    output = StringIO()
    status = await run_cli(
        [
            "kubernetes",
            "check",
            LOCAL_PROFILE_NAME,
            "--workload",
            "api",
            "--namespace",
            "prod",
        ],
        stdout=output,
    )
    payload = read_json(output.getvalue())

    assert status == 0
    assert payload["status"] == "ok"
    print(
        json.dumps(
            {
                "status": payload["status"],
                "model_probe": cast(dict[str, Any], payload["model_probe"])["status"],
                "preflight": cast(dict[str, Any], payload["preflight"])["status"],
                "contract": cast(dict[str, Any], payload["contract"])["status"],
                "next_step": cast(dict[str, Any], payload["next_step"])["type"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
