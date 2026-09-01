from __future__ import annotations

import asyncio
import json
from io import StringIO
from typing import Any, cast

from universal_agent_cli import run_cli


def read_json(raw: str) -> dict[str, Any]:
    loaded: object = json.loads(raw)
    assert isinstance(loaded, dict)
    return cast(dict[str, Any], loaded)


async def main() -> None:
    output = StringIO()
    status = await run_cli(["config", "show"], stdout=output)
    payload = read_json(output.getvalue())

    store = cast(dict[str, Any], payload["store"])
    limits = cast(dict[str, Any], payload["limits"])
    domains = cast(list[dict[str, Any]], payload["domains"])

    assert status == 0
    assert store["backend"] == "memory"
    assert limits["max_iterations"] == 20
    assert domains[0]["name"] == "kubernetes"
    print(
        json.dumps(
            {
                "store_backend": store["backend"],
                "max_iterations": limits["max_iterations"],
                "primary_domain": domains[0]["name"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
