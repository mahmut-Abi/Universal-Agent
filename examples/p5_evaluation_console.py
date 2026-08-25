from __future__ import annotations

import asyncio
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent.cli import LOCAL_PROFILE_NAME, run_cli


async def main() -> None:
    with TemporaryDirectory() as directory:
        report_dir = Path(directory) / "reports"

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

        output = StringIO()
        status = await run_cli(
            ["eval", "console", "--report-dir", str(report_dir)],
            stdout=output,
        )
        html = output.getvalue()
        text_output = StringIO()
        text_status = await run_cli(
            ["eval", "console", "--report-dir", str(report_dir), "--format", "text"],
            stdout=text_output,
        )
        text = text_output.getvalue()

    print(f"status={status}")
    print("\n".join(html.splitlines()[:20]))
    print(f"text_status={text_status}")
    print("\n".join(text.splitlines()[:12]))


if __name__ == "__main__":
    asyncio.run(main())
