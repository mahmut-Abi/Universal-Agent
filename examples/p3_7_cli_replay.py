from __future__ import annotations

import asyncio
import json
from io import StringIO
from tempfile import TemporaryDirectory
from typing import Any

from universal_agent.cli import run_cli


def read_json(buffer: StringIO) -> dict[str, Any]:
    loaded: object = json.loads(buffer.getvalue())
    assert isinstance(loaded, dict)
    return loaded


async def main() -> None:
    with TemporaryDirectory() as directory:
        record_output = StringIO()
        record_status = await run_cli(
            [
                "eval",
                "replay",
                "local-kubernetes",
                "--recording-dir",
                directory,
                "--kind",
                "regression",
                "--update",
            ],
            stdout=record_output,
        )
        replay_output = StringIO()
        replay_status = await run_cli(
            [
                "eval",
                "replay",
                "local-kubernetes",
                "--recording-dir",
                directory,
                "--kind",
                "regression",
                "--fail-on-fail",
            ],
            stdout=replay_output,
        )

        record_payload = read_json(record_output)
        replay_payload = read_json(replay_output)

    print(f"record_status={record_status} recorded={record_payload['scenario_count']}")
    print(f"replay_status={replay_status} passed={replay_payload['passed']}")


if __name__ == "__main__":
    asyncio.run(main())
