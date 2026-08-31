from __future__ import annotations

from pathlib import Path

from universal_agent.core import JsonMapping, immutable_json
from universal_agent.profile import ProfileConfig, ProfileConfigNotFoundError
from universal_agent.security import (
    SecretProvider,
    SecretResolutionReport,
    redact_sensitive_mapping,
    resolve_secret_refs,
)


def validate_profile_config_file(
    path: str | Path,
    *,
    check_secrets: bool = True,
    secret_provider: SecretProvider | None = None,
) -> JsonMapping:
    profile_path = Path(path)
    if not profile_path.is_file():
        raise ProfileConfigNotFoundError(f"profile config not found: {profile_path}")

    profile_config = ProfileConfig.from_json_file(profile_path)
    profile = profile_config.to_profile()
    runtime = profile.runtime
    secret_resolution = (
        resolve_secret_refs(runtime.secrets, provider=secret_provider) if check_secrets else None
    )
    secrets_ok = True if secret_resolution is None else secret_resolution.passed

    return immutable_json(
        {
            "status": "ok" if secrets_ok else "error",
            "profile_config": str(profile_path),
            "profile": {
                "name": profile.name,
                "version": profile.version,
                "description": profile.description,
            },
            "runtime": {
                "environment": dict(redact_sensitive_mapping(runtime.environment)),
                "domain_count": len(profile.configured_domains()),
                "domain_package_paths": list(runtime.domain_package_paths),
                "model": {
                    "provider": runtime.model.provider.value,
                    "name": runtime.model.name,
                    "endpoint": runtime.model.endpoint,
                    "api_key_secret": runtime.model.api_key_secret,
                    "timeout_seconds": runtime.model.timeout_seconds,
                    "response_format": runtime.model.response_format,
                },
                "store": {
                    "backend": runtime.store.backend.value,
                    "path": runtime.store.path,
                },
                "distributed_queue": {
                    "backend": runtime.distributed_queue.backend.value,
                    "path": runtime.distributed_queue.path,
                },
                "distributed_locks": {
                    "backend": runtime.distributed_locks.backend.value,
                    "path": runtime.distributed_locks.path,
                },
                "distributed_workers": {
                    "backend": runtime.distributed_workers.backend.value,
                    "path": runtime.distributed_workers.path,
                },
                "limits": {
                    "max_iterations": runtime.limits.max_iterations,
                    "max_recovery_steps": runtime.limits.max_recovery_steps,
                },
            },
            "secrets": {
                "status": _secret_status(secret_resolution),
                "checked": check_secrets,
                "declared": len(runtime.secrets),
                "available": 0
                if secret_resolution is None
                else sum(1 for item in secret_resolution.items if item.available),
                "missing_required": []
                if secret_resolution is None
                else list(secret_resolution.missing_required_names),
            },
        }
    )


def _secret_status(secret_resolution: SecretResolutionReport | None) -> str:
    if secret_resolution is None:
        return "not_checked"
    if secret_resolution.passed:
        return "ok"
    return "missing_required"
