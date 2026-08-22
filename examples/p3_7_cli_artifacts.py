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


async def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        report_dir = root / "reports"
        recording_dir = root / "recordings"

        await run_cli(
            [
                "eval",
                "run",
                LOCAL_PROFILE_NAME,
                "--kind",
                "regression",
                "--report-dir",
                str(report_dir),
            ],
            stdout=StringIO(),
        )
        reports_output = StringIO()
        reports_status = await run_cli(
            ["eval", "reports", "--report-dir", str(report_dir)],
            stdout=reports_output,
        )

        await run_cli(
            [
                "eval",
                "replay",
                LOCAL_PROFILE_NAME,
                "--kind",
                "regression",
                "--recording-dir",
                str(recording_dir),
                "--update",
            ],
            stdout=StringIO(),
        )
        recordings_output = StringIO()
        recordings_status = await run_cli(
            ["eval", "recordings", "--recording-dir", str(recording_dir)],
            stdout=recordings_output,
        )

        reports = read_json(reports_output)
        recordings = read_json(recordings_output)

    print(f"reports_status={reports_status} reports={reports['report_count']}")
    print(f"recordings_status={recordings_status} recordings={recordings['recording_count']}")


if __name__ == "__main__":
    asyncio.run(main())
