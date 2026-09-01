from __future__ import annotations

import asyncio
import json
from io import StringIO
from typing import Any

from universal_agent_cli import LOCAL_PROFILE_NAME, build_default_service, run_cli


def read_json(buffer: StringIO) -> dict[str, Any]:
    loaded: object = json.loads(buffer.getvalue())
    assert isinstance(loaded, dict)
    return loaded


async def main() -> None:
    service = build_default_service()
    run_output = StringIO()
    await run_cli(
        ["run", LOCAL_PROFILE_NAME, "Verify workload health"],
        service=service,
        stdout=run_output,
    )
    run_payload = read_json(run_output)
    session_id = run_payload["result"]["session_id"]
    assert isinstance(session_id, str)

    tui_output = StringIO()
    status = await run_cli(
        ["tui", "--session-id", session_id, "--event-limit", "20"],
        service=service,
        stdout=tui_output,
    )

    print(f"status={status}")
    print(tui_output.getvalue())


if __name__ == "__main__":
    asyncio.run(main())
