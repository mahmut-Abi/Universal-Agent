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
        ["run", LOCAL_PROFILE_NAME, "Verify the local example workload is healthy"],
        stdout=output,
    )
    payload = read_json(output.getvalue())
    result = cast(dict[str, Any], payload["result"])
    session = cast(dict[str, Any], payload["session"])

    assert status == 0
    assert result["status"] == "completed"
    assert session["goal_description"] == "Verify the local example workload is healthy"
    print(json.dumps({"status": result["status"], "session_id": session["session_id"]}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
