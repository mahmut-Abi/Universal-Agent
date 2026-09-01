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


async def main() -> None:
    with TemporaryDirectory() as tmpdir:
        profile_path = Path(tmpdir) / "profile.json"
        init_output = StringIO()
        validate_output = StringIO()

        init_status = await run_cli(
            [
                "init",
                "--output",
                str(profile_path),
                "--model-provider",
                "json_http",
                "--model-name",
                "runtime-decider",
                "--model-endpoint",
                "https://models.example.test/decide",
                "--model-api-key-env",
                "RUNTIME_MODEL_API_KEY",
            ],
            stdout=init_output,
        )
        validate_status = await run_cli(
            [
                "--profile-config",
                str(profile_path),
                "config",
                "validate",
                "--skip-secret-resolution",
            ],
            stdout=validate_output,
        )
        report = read_json(validate_output.getvalue())

    assert init_status == 0
    assert validate_status == 0
    assert report["status"] == "ok"
    assert report["runtime"]["model"]["provider"] == "json_http"
    assert report["secrets"]["status"] == "not_checked"
    print(
        json.dumps(
            {
                "status": report["status"],
                "model_provider": report["runtime"]["model"]["provider"],
                "secrets": report["secrets"]["status"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
