"""`agent init` — first-time Golden Path setup.

Creates a local, human-readable configuration tree:

    universal-agent/
      config.json     machine- and human-readable agent-level settings
      profile.json    AgentProfile config consumed by the runtime

`init` is idempotent: without `--force` an existing tree is reused (missing
files are filled in). `--force` rewrites the files and keeps `.bak` backups of
overwritten ones.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO, cast

from universal_agent.core import write_json_file
from universal_agent_cli.defaults import default_init_output_path, global_init_output_path
from universal_agent_cli.io import _parse_key_value_options, _write_json, _write_text

DEFAULT_ENVIRONMENT = "local"
DEFAULT_MAX_ITERATIONS = 20
DEFAULT_MAX_RECOVERY_STEPS = 8


@dataclass(frozen=True, slots=True)
class ModelProviderPreset:
    provider: str
    model_name: str
    response_format: str | None
    timeout_seconds: float


MODEL_PROVIDER_PRESETS: dict[str, ModelProviderPreset] = {
    "360zhinao": ModelProviderPreset(
        "openai_chat_completions",
        "glm-5.3-flash",
        "prompt_json",
        180.0,
    ),
    "deepseek": ModelProviderPreset(
        "openai_chat_completions",
        "deepseek-chat",
        "json_object",
        120.0,
    ),
    "moonshot": ModelProviderPreset(
        "openai_chat_completions",
        "moonshot-v1-8k",
        "json_object",
        120.0,
    ),
}


def _resolved_model_settings(args: argparse.Namespace) -> ModelProviderPreset:
    preset_name = cast(str | None, getattr(args, "model_provider_preset", None))
    provider = cast(str, args.model_provider)
    model_name = cast(str, args.model_name)
    response_format = cast(str | None, args.model_response_format)
    timeout_seconds = cast(float, args.model_timeout_seconds)
    if preset_name is None:
        return ModelProviderPreset(provider, model_name, response_format, timeout_seconds)
    preset = MODEL_PROVIDER_PRESETS[preset_name]
    if provider != "scripted" and provider != preset.provider:
        raise ValueError("--model-provider conflicts with --model-provider-preset")
    if model_name != "scripted" and model_name != preset.model_name:
        raise ValueError("--model-name conflicts with --model-provider-preset")
    if response_format is not None and response_format != preset.response_format:
        raise ValueError("--model-response-format conflicts with --model-provider-preset")
    return ModelProviderPreset(
        preset.provider,
        preset.model_name if model_name == "scripted" else model_name,
        preset.response_format if response_format is None else response_format,
        timeout_seconds if timeout_seconds != 30.0 else preset.timeout_seconds,
    )


def _dispatch_init(args: argparse.Namespace, out: TextIO) -> None:
    output = _init_output_path(args)
    config_dir = output.parent
    config_path = config_dir / "config.json"
    force = cast(bool, args.force)
    reuse = output.exists() and not force
    if config_path.exists() and not force:
        reuse = True
    backups: list[str] = []
    if force:
        backups.extend(_backup_existing((output, config_path)))
    config_dir.mkdir(parents=True, exist_ok=True)
    profile_name = cast(str, args.profile)

    runtime_payload = _runtime_payload(args)
    domain_payload = cast(dict[str, object], runtime_payload["domain"])
    profile_payload = _profile_config_payload(
        profile_name=profile_name,
        environment=cast(str, args.environment),
        runtime_payload=runtime_payload,
        domain_payload=domain_payload,
    )
    if not output.exists() or force:
        write_json_file(output, profile_payload, indent=True)
    if not config_path.exists() or force:
        write_json_file(config_path, _user_config_payload(args), indent=True)

    payload: dict[str, object] = {
        "status": "reused" if reuse else "created",
        "profile": profile_name,
        "path": str(output),
        "config": str(config_path),
        "data_dir": str(_runtime_data_dir(args)),
    }
    if backups:
        payload["backups"] = backups
    if cast(str, args.output_format) == "json":
        _write_json(out, payload)
        return
    model_settings = _resolved_model_settings(args)
    _write_text(
        out,
        "Universal Agent setup complete.\n"
        f"  Profile config : {payload['path']}\n"
        f"  Settings       : {payload['config']}\n"
        f"  Profile        : {profile_name}\n"
        f"  Model          : {model_settings.provider} / {model_settings.model_name}\n"
        f"  Data dir       : {payload['data_dir']}\n"
        'Next: `agent doctor` then `agent run "your goal"`.\n',
    )


def _init_output_path(args: argparse.Namespace) -> Path:
    explicit = cast(str | None, args.output)
    if explicit is not None:
        return Path(explicit)
    if cast(bool, args.global_config):
        return Path(global_init_output_path())
    return Path(default_init_output_path())


def _backup_existing(paths: tuple[Path, ...]) -> list[str]:
    backups: list[str] = []
    for path in paths:
        if path.exists():
            backup = path.with_suffix(path.suffix + ".bak")
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            backups.append(str(backup))
    return backups


def _runtime_data_dir(args: argparse.Namespace) -> Path:
    store_path = Path(cast(str, args.store_path))
    if cast(str, args.store_backend) == "memory":
        return _init_output_path(args).parent
    return store_path.parent


def _user_config_payload(args: argparse.Namespace) -> dict[str, object]:
    domain_name = _domain_settings(args)[0]
    model_settings = _resolved_model_settings(args)
    payload: dict[str, object] = {
        "environment": cast(str, args.environment),
        "data_dir": str(_runtime_data_dir(args)),
        "profile": cast(str, args.profile),
        "model": {
            "provider": model_settings.provider,
            "name": model_settings.model_name,
        },
        "policy": {"mode": "safe"},
        "runtime": {
            "max_steps": DEFAULT_MAX_ITERATIONS,
            "max_recovery_steps": DEFAULT_MAX_RECOVERY_STEPS,
            "store_backend": cast(str, args.store_backend),
        },
        "domains": {
            domain_name: {
                "enabled": True,
                "backend": cast(str, args.domain_backend),
            }
        },
    }
    # Client/server separation: a configured `server` section turns this
    # machine into a thin client that talks to a remote agentd Runtime.
    server_url = cast("str | None", getattr(args, "server_url", None))
    if server_url:
        server: dict[str, object] = {"url": server_url}
        server_token_env = cast("str | None", getattr(args, "server_auth_token_env", None))
        if server_token_env:
            server["auth_token_env"] = server_token_env
        payload["server"] = server
    return payload


def _domain_settings(
    args: argparse.Namespace,
) -> tuple[str, dict[str, object], dict[str, dict[str, object]]]:
    """Resolve the init domain via domain contributions.

    Resolution is two-pass so that any domain is reachable regardless of
    alphabetical entry-point order (the domain-neutral local contribution
    accepts unconditionally and would otherwise shadow every domain sorted
    after it):

    1. Contributions that explicitly declare the requested ``--domain-backend``
       in their ``init_backends`` get first claim.
    2. All remaining contributions in discovery order; the unconditional
       local fallback still catches the default/unknown backends.
    """

    from universal_agent_cli.contributions import load_cli_contributions

    contributions = [
        item for item in load_cli_contributions() if item.init_resolve_domain is not None
    ]
    requested_backend = getattr(args, "domain_backend", None)
    claimed = [
        item
        for item in contributions
        if requested_backend is not None and requested_backend in item.init_backends
    ]
    for contribution in [*claimed, *(item for item in contributions if item not in claimed)]:
        resolve = contribution.init_resolve_domain
        if resolve is None:
            continue
        outcome = resolve(args)
        if outcome is not None:
            return (
                outcome.domain_name,
                outcome.domain_config,
                {name: dict(spec) for name, spec in outcome.secrets.items()},
            )
    # No domain contribution installed: fall back to the domain-neutral
    # local profile shape (matches domains/local's `local_domain_config`).
    return "local", {"name": "local", "version": "0.1.0"}, {}


def _runtime_payload(args: argparse.Namespace) -> dict[str, object]:
    model_settings = _resolved_model_settings(args)
    model_secret_source = _single_secret_source(
        "--model-api-key",
        env_key=cast(str | None, args.model_api_key_env),
        file_path=cast(str | None, args.model_api_key_file),
    )
    _domain_name, domain, domain_secrets = _domain_settings(args)
    model_secret_name = cast(str, args.model_api_key_secret)
    store: dict[str, str] = {"backend": cast(str, args.store_backend)}
    if cast(str, args.store_backend) != "memory":
        store["path"] = cast(str, args.store_path)
    distributed_queue: dict[str, str] = {"backend": cast(str, args.distributed_queue_backend)}
    if cast(str, args.distributed_queue_backend) != "memory":
        distributed_queue["path"] = cast(str, args.distributed_queue_path)
    distributed_locks: dict[str, str] = {"backend": cast(str, args.distributed_locks_backend)}
    if cast(str, args.distributed_locks_backend) != "memory":
        distributed_locks["path"] = cast(str, args.distributed_locks_path)
    distributed_workers: dict[str, str] = {"backend": cast(str, args.distributed_workers_backend)}
    if cast(str, args.distributed_workers_backend) != "memory":
        distributed_workers["path"] = cast(str, args.distributed_workers_path)
    runtime: dict[str, object] = {
        "environment": {"environment": cast(str, args.environment)},
        "model": _profile_model_config(
            model_provider=model_settings.provider,
            model_name=model_settings.model_name,
            model_endpoint=cast(str | None, args.model_endpoint),
            model_api_key_source=model_secret_source,
            model_api_key_secret=model_secret_name,
            model_timeout_seconds=model_settings.timeout_seconds,
            model_response_format=model_settings.response_format,
            model_headers=_parse_key_value_options(
                cast(list[str], args.model_header),
                "model-header",
            ),
        ),
        "store": store,
        "distributed_queue": distributed_queue,
        "distributed_locks": distributed_locks,
        "distributed_workers": distributed_workers,
        "limits": {
            "max_iterations": DEFAULT_MAX_ITERATIONS,
            "max_recovery_steps": DEFAULT_MAX_RECOVERY_STEPS,
        },
        "domain": domain,
    }
    if cast(float | None, args.distributed_terminal_retention_seconds) is not None:
        runtime["distributed_terminal_retention_seconds"] = cast(
            float, args.distributed_terminal_retention_seconds
        )
    secrets: dict[str, dict[str, object]] = {}
    if model_secret_source is not None:
        _add_secret(secrets, model_secret_name, model_secret_source)
    for secret_name, secret_spec in domain_secrets.items():
        if secret_name in secrets:
            raise ValueError(f"duplicate runtime secret: {secret_name}")
        secrets[secret_name] = secret_spec
    if secrets:
        runtime["secrets"] = secrets
    return runtime


def _profile_config_payload(
    *,
    profile_name: str,
    environment: str,
    runtime_payload: dict[str, object],
    domain_payload: dict[str, object],
) -> dict[str, object]:
    domain_name = str(domain_payload.get("name", "local"))
    description = (
        "Generic local Agent profile created by `agent init`."
        if domain_name == "local"
        else f"{domain_name.capitalize()} Agent profile created by `agent init`."
    )
    return {
        "name": profile_name,
        "version": "0.1.0",
        "description": description,
        "domain": domain_payload,
        "runtime": runtime_payload,
    }


def _single_secret_source(
    label: str,
    *,
    env_key: str | None,
    file_path: str | None,
) -> tuple[str, str] | None:
    if env_key is not None and file_path is not None:
        raise ValueError(f"{label} accepts either env or file, not both")
    if env_key is not None:
        return ("env", env_key)
    if file_path is not None:
        return ("file", file_path)
    return None


def _add_secret(
    secrets: dict[str, dict[str, object]],
    name: str,
    source: tuple[str, str],
) -> None:
    source_name, key = source
    if not name.strip():
        raise ValueError("secret name must not be empty")
    if not key.strip():
        raise ValueError(f"secret {name} {source_name} key must not be empty")
    if name in secrets:
        raise ValueError(f"duplicate runtime secret: {name}")
    secrets[name] = {"source": source_name, "key": key, "required": True}


def _profile_model_config(
    *,
    model_provider: str,
    model_name: str,
    model_endpoint: str | None,
    model_api_key_source: tuple[str, str] | None,
    model_api_key_secret: str,
    model_timeout_seconds: float,
    model_response_format: str | None,
    model_headers: dict[str, str],
) -> dict[str, object]:
    model: dict[str, object] = {
        "provider": model_provider,
        "name": model_name,
        "timeout_seconds": model_timeout_seconds,
    }
    if model_provider == "scripted":
        if model_endpoint is not None:
            raise ValueError("scripted model does not accept --model-endpoint")
        if model_api_key_source is not None:
            raise ValueError("scripted model does not accept model API key secrets")
        if model_response_format is not None:
            raise ValueError("scripted model does not accept --model-response-format")
        if model_headers:
            raise ValueError("scripted model does not accept --model-header")
        return model
    if model_provider == "json_http":
        if model_endpoint is None or not model_endpoint.strip():
            raise ValueError("json_http model requires --model-endpoint")
        if model_response_format is not None:
            raise ValueError("json_http model does not accept --model-response-format")
        model["endpoint"] = model_endpoint
    elif model_provider in {"openai_chat_completions", "openai_responses"}:
        if model_name == "scripted":
            raise ValueError(f"{model_provider} model requires --model-name")
        if model_api_key_source is None:
            raise ValueError(f"{model_provider} model requires model API key secret")
        if model_endpoint is not None:
            if not model_endpoint.strip():
                raise ValueError(f"{model_provider} model endpoint must not be empty")
            model["endpoint"] = model_endpoint
        if model_response_format is not None:
            if model_provider != "openai_chat_completions":
                raise ValueError(f"{model_provider} model does not accept --model-response-format")
            model["response_format"] = model_response_format
    else:
        raise ValueError(f"unsupported model provider: {model_provider}")
    if model_api_key_source is not None:
        model["api_key_secret"] = model_api_key_secret
    if model_headers:
        model["headers"] = model_headers
    return model
