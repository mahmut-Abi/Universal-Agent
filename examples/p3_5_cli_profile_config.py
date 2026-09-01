from __future__ import annotations

import asyncio
import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from universal_agent_cli import run_cli


def read_json(raw: str) -> dict[str, Any]:
    loaded: object = json.loads(raw)
    assert isinstance(loaded, dict)
    return cast(dict[str, Any], loaded)


async def run_agent_command(args: list[str]) -> dict[str, Any]:
    output = StringIO()
    status = await run_cli(args, stdout=output)
    assert status == 0
    return read_json(output.getvalue())


async def main() -> None:
    with TemporaryDirectory(prefix="universal-agent-cli-profile-") as directory:
        root = Path(directory)
        profile_path = root / "profile.json"
        store_path = root / "store"

        await run_agent_command(
            [
                "init",
                "--output",
                str(profile_path),
                "--profile",
                "configured-operator",
                "--environment",
                "production",
                "--store-backend",
                "file",
                "--store-path",
                str(store_path),
            ]
        )
        run_payload = await run_agent_command(
            [
                "--profile-config",
                str(profile_path),
                "run",
                "configured-operator",
                "Verify configured workload health",
            ]
        )
        list_payload = await run_agent_command(
            ["--profile-config", str(profile_path), "session", "list"]
        )
        config_payload = await run_agent_command(
            ["--profile-config", str(profile_path), "config", "show"]
        )

        result = cast(dict[str, Any], run_payload["result"])
        sessions = cast(list[dict[str, Any]], list_payload["sessions"])
        store = cast(dict[str, Any], config_payload["store"])
        print(
            json.dumps(
                {
                    "profile": str(profile_path),
                    "store_backend": store["backend"],
                    "result_status": result["status"],
                    "session_count": len(sessions),
                    "session_id": result["session_id"],
                },
                indent=2,
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
