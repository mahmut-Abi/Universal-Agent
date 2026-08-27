from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from universal_agent.ecosystem.models import (
    EcosystemRegistryManifest,
    EcosystemRegistryStoreNotFoundError,
    EcosystemRegistryWriteResult,
)
from universal_agent.ecosystem.registry_codec import (
    load_ecosystem_registry_manifest,
    write_ecosystem_registry_manifest,
)
from universal_agent.ecosystem.registry_index import EcosystemRegistryIndex
from universal_agent.ecosystem.validation import _require_non_empty


class FileEcosystemRegistryStore:
    """File-backed store for exported ecosystem registry manifests.

    The store is a local package-registry primitive. It persists registry
    manifests and lists them by metadata identity, but it does not inspect
    package roots, import Domain code, run evaluation suites or assemble hosts.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def save(
        self,
        manifest: EcosystemRegistryManifest,
        *,
        overwrite: bool = True,
    ) -> EcosystemRegistryWriteResult:
        return write_ecosystem_registry_manifest(
            self._path(manifest.name, manifest.version),
            manifest,
            overwrite=overwrite,
        )

    def load(self, name: str, version: str) -> EcosystemRegistryManifest:
        path = self._path(name, version)
        if not path.exists():
            raise EcosystemRegistryStoreNotFoundError(
                f"ecosystem registry manifest not found: {name}@{version}"
            )
        return load_ecosystem_registry_manifest(path)

    def index(self, name: str, version: str) -> EcosystemRegistryIndex:
        return EcosystemRegistryIndex(self.load(name, version))

    def list_manifests(self) -> tuple[EcosystemRegistryManifest, ...]:
        if not self._root.exists():
            return ()
        manifests = tuple(
            load_ecosystem_registry_manifest(path) for path in sorted(self._root.glob("*.json"))
        )
        return tuple(sorted(manifests, key=lambda item: (item.name, item.version)))

    def _path(self, name: str, version: str) -> Path:
        _require_non_empty(name, "registry name")
        _require_non_empty(version, "registry version")
        return self._root / f"{quote(name, safe='')}@{quote(version, safe='')}.json"


