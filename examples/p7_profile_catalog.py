from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from universal_agent import load_profile_catalog


def profile_payload(name: str, domain: str) -> dict[str, object]:
    return {
        "name": name,
        "version": "1.0.0",
        "description": f"{name} profile",
        "domain": {"name": domain, "version": "0.2.0"},
        "runtime": {
            "domain": {"name": domain, "version": "0.2.0"},
            "environment": {"environment": "local"},
        },
    }


def write_profile(path: Path, name: str, domain: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile_payload(name, domain), indent=2), encoding="utf-8")


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        write_profile(root / "kubernetes.profile.json", "kubernetes-operator", "kubernetes")
        write_profile(root / "database" / "profile.json", "database-operator", "database")

        catalog = load_profile_catalog(root)

        print(f"profiles={','.join(entry.profile.name for entry in catalog.all())}")
        print(f"registry_has_kubernetes={catalog.registry().has('kubernetes-operator')}")
        print(f"database_domain={catalog.get('database-operator').profile.domain.name}")


if __name__ == "__main__":
    main()
