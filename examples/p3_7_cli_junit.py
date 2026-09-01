from __future__ import annotations

import asyncio
from io import StringIO
from xml.etree.ElementTree import fromstring

from universal_agent_cli import LOCAL_PROFILE_NAME, run_cli


async def main() -> None:
    output = StringIO()
    status = await run_cli(
        [
            "eval",
            "run",
            LOCAL_PROFILE_NAME,
            "--kind",
            "regression",
            "--tag",
            "smoke",
            "--format",
            "junit",
            "--fail-on-fail",
        ],
        stdout=output,
    )
    root = fromstring(output.getvalue())

    print(f"status={status}")
    print(f"suite={root.attrib['name']}")
    print(f"tests={root.attrib['tests']} failures={root.attrib['failures']}")


if __name__ == "__main__":
    asyncio.run(main())
