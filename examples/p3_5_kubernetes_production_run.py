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
            "run",
            LOCAL_PROFILE_NAME,
            "--workload",
            "example",
        ],
        stdout=output,
    )
    payload = read_json(output.getvalue())
    model_probe = cast(dict[str, Any], payload["model_probe"])
    run = cast(dict[str, Any], payload["run"])
    result = cast(dict[str, Any], run["result"])
    session = cast(dict[str, Any], run["session"])

    assert status == 0
    assert payload["status"] == "completed"
    assert payload["operation"]["workload"] == "deployment/example"
    assert model_probe["status"] == "ok"
    assert result["status"] == "completed"
    print(
        json.dumps(
            {
                "status": payload["status"],
                "model_probe": model_probe["status"],
                "session_id": session["session_id"],
                "goal": session["goal_description"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
