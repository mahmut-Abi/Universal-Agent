from __future__ import annotations

from pathlib import Path

from universal_agent.core import JsonMapping, write_json
from universal_agent.domain.package_codec import (
    build_domain_package_manifest,
    encode_domain_package_manifest,
    load_domain_package,
)
from universal_agent.domain.package_models import (
    DOMAIN_PACKAGE_DIRECTORIES,
    DOMAIN_PACKAGE_MANIFEST,
    DomainPackageManifest,
    DomainPackageRuntimeLoadError,
    DomainPackageScaffoldResult,
    DomainPackageScaffoldSpec,
    DomainPackageValidationError,
    _validate_package_resource,
)
from universal_agent.domain.package_runtime_loader import _parse_entrypoint
from universal_agent.domain.package_runtime_stub import runtime_stub_source


def scaffold_domain_package(
    root: Path,
    spec: DomainPackageScaffoldSpec,
    *,
    overwrite: bool = False,
) -> DomainPackageScaffoldResult:
    """Create a package skeleton that can be validated by DomainPackageRegistry."""

    manifest = build_domain_package_manifest(spec)
    manifest_path = root / DOMAIN_PACKAGE_MANIFEST
    if manifest_path.exists() and not overwrite:
        raise DomainPackageValidationError(
            f"domain package manifest already exists: {manifest_path}"
        )

    created_paths: list[Path] = []
    if not root.exists():
        root.mkdir(parents=True)
        created_paths.append(root)
    elif not root.is_dir():
        raise DomainPackageValidationError(f"domain package root must be a directory: {root}")

    for directory_name in DOMAIN_PACKAGE_DIRECTORIES:
        directory = root / directory_name
        if not directory.exists():
            directory.mkdir()
            created_paths.append(directory)
        elif not directory.is_dir():
            raise DomainPackageValidationError(
                f"domain package scaffold path must be a directory: {directory}"
            )

    for resource in spec.resources:
        _validate_package_resource(resource)
        resource_path = root / resource
        if resource_path.suffix:
            directory = resource_path.parent
        else:
            directory = resource_path
        if not directory.exists():
            directory.mkdir(parents=True)
            created_paths.append(directory)
        elif not directory.is_dir():
            raise DomainPackageValidationError(
                f"domain package scaffold resource parent must be a directory: {directory}"
            )
        if resource_path.suffix and not resource_path.exists():
            resource_path.touch()
            created_paths.append(resource_path)

    written_paths: list[Path] = []
    runtime_stub_paths: tuple[Path, ...] = ()
    if spec.runtime_stub:
        runtime_stub_result = _write_runtime_stub(root, manifest, overwrite=overwrite)
        created_paths.extend(runtime_stub_result[0])
        written_paths.extend(runtime_stub_result[1])
        runtime_stub_paths = tuple(runtime_stub_result[1])

    overwritten = manifest_path.exists()
    _write_json_manifest(manifest_path, encode_domain_package_manifest(manifest))
    written_paths.append(manifest_path)
    package = load_domain_package(root)
    return DomainPackageScaffoldResult(
        package=package,
        created_paths=tuple(created_paths),
        written_paths=tuple(written_paths),
        runtime_stub_paths=runtime_stub_paths,
        overwritten=overwritten,
    )


def _write_json_manifest(path: Path, payload: JsonMapping) -> None:
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        write_json(handle, payload, indent=True)
    tmp_path.replace(path)


def _write_runtime_stub(
    root: Path,
    manifest: DomainPackageManifest,
    *,
    overwrite: bool,
) -> tuple[list[Path], list[Path]]:
    if not manifest.evaluators:
        raise DomainPackageValidationError("runtime stub requires at least one evaluator")
    if manifest.tools and not manifest.capabilities:
        raise DomainPackageValidationError("runtime stub tools require at least one capability")
    if manifest.entrypoint is None:
        raise DomainPackageValidationError("runtime stub requires an entrypoint")

    try:
        module_name, attribute_path = _parse_entrypoint(manifest.entrypoint)
    except DomainPackageRuntimeLoadError as exc:
        raise DomainPackageValidationError(f"invalid runtime stub entrypoint: {exc}") from exc
    if len(attribute_path) != 1:
        raise DomainPackageValidationError("runtime stub entrypoint must name one factory function")
    if not all(part.isidentifier() for part in module_name.split(".")):
        raise DomainPackageValidationError(
            f"runtime stub entrypoint module is not a valid Python module: {module_name}"
        )
    factory_name = attribute_path[0]
    if not factory_name.isidentifier():
        raise DomainPackageValidationError(
            f"runtime stub entrypoint factory is not a valid Python identifier: {factory_name}"
        )

    created_paths: list[Path] = []
    written_paths: list[Path] = []
    module_parts = module_name.split(".")
    if len(module_parts) == 1:
        module_path = root / f"{module_parts[0]}.py"
    else:
        package_dir = root
        for package_part in module_parts[:-1]:
            package_dir = package_dir / package_part
            if not package_dir.exists():
                package_dir.mkdir()
                created_paths.append(package_dir)
            elif not package_dir.is_dir():
                raise DomainPackageValidationError(
                    f"runtime stub package path must be a directory: {package_dir}"
                )
            init_path = package_dir / "__init__.py"
            if not init_path.exists():
                init_path.write_text("", encoding="utf-8")
                written_paths.append(init_path)
        module_path = package_dir / f"{module_parts[-1]}.py"

    if module_path.exists() and not overwrite:
        raise DomainPackageValidationError(
            f"domain package runtime stub already exists: {module_path}"
        )
    module_path.write_text(
        runtime_stub_source(manifest, factory_name),
        encoding="utf-8",
    )
    written_paths.append(module_path)
    return created_paths, written_paths
