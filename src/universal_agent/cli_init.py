from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TextIO, cast

from universal_agent.cli_io import _parse_key_value_options, _write_json
from universal_agent.domains.kubernetes.cli import (
    profile_domain_config as kubernetes_profile_domain_config,
)


def _dispatch_init(args: argparse.Namespace, out: TextIO) -> None:
    output = Path(cast(str, args.output))
    if output.exists() and not cast(bool, args.force):
        raise ValueError(f"profile config already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    profile_name = cast(str, args.profile)
    payload = _profile_config_payload(
        profile_name=profile_name,
        environment=cast(str, args.environment),
        store_backend=cast(str, args.store_backend),
        store_path=cast(str, args.store_path),
        distributed_queue_backend=cast(str, args.distributed_queue_backend),
        distributed_queue_path=cast(str, args.distributed_queue_path),
        distributed_locks_backend=cast(str, args.distributed_locks_backend),
        distributed_locks_path=cast(str, args.distributed_locks_path),
        distributed_workers_backend=cast(str, args.distributed_workers_backend),
        distributed_workers_path=cast(str, args.distributed_workers_path),
        distributed_terminal_retention_seconds=cast(
            float | None, args.distributed_terminal_retention_seconds
        ),
        domain_backend=cast(str, args.domain_backend),
        kubectl_namespace=cast(str, args.kubectl_namespace),
        kubectl_context=cast(str | None, args.kubectl_context),
        kubectl_kubeconfig=cast(str | None, args.kubectl_kubeconfig),
        kubectl_timeout_seconds=cast(float, args.kubectl_timeout_seconds),
        kubernetes_api_server=cast(str | None, args.kubernetes_api_server),
        kubernetes_api_namespace=cast(str, args.kubernetes_api_namespace),
        kubernetes_api_token_env=cast(str | None, args.kubernetes_api_token_env),
        kubernetes_api_token_file=cast(str | None, args.kubernetes_api_token_file),
        kubernetes_api_token_secret=cast(str, args.kubernetes_api_token_secret),
        kubernetes_api_timeout_seconds=cast(float, args.kubernetes_api_timeout_seconds),
        model_provider=cast(str, args.model_provider),
        model_name=cast(str, args.model_name),
        model_endpoint=cast(str | None, args.model_endpoint),
        model_api_key_env=cast(str | None, args.model_api_key_env),
        model_api_key_file=cast(str | None, args.model_api_key_file),
        model_api_key_secret=cast(str, args.model_api_key_secret),
        model_timeout_seconds=cast(float, args.model_timeout_seconds),
        model_response_format=cast(str | None, args.model_response_format),
        model_headers=_parse_key_value_options(cast(list[str], args.model_header), "model-header"),
    )
    tmp_path = output.with_name(output.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(output)
    _write_json(out, {"status": "created", "profile": profile_name, "path": str(output)})

def _profile_config_payload(
    *,
    profile_name: str,
    environment: str,
    store_backend: str,
    store_path: str,
    distributed_queue_backend: str,
    distributed_queue_path: str,
    distributed_locks_backend: str,
    distributed_locks_path: str,
    distributed_workers_backend: str,
    distributed_workers_path: str,
    distributed_terminal_retention_seconds: float | None,
    domain_backend: str,
    kubectl_namespace: str,
    kubectl_context: str | None,
    kubectl_kubeconfig: str | None,
    kubectl_timeout_seconds: float,
    kubernetes_api_server: str | None,
    kubernetes_api_namespace: str,
    kubernetes_api_token_env: str | None,
    kubernetes_api_token_file: str | None,
    kubernetes_api_token_secret: str,
    kubernetes_api_timeout_seconds: float,
    model_provider: str,
    model_name: str,
    model_endpoint: str | None,
    model_api_key_env: str | None,
    model_api_key_file: str | None,
    model_api_key_secret: str,
    model_timeout_seconds: float,
    model_response_format: str | None,
    model_headers: dict[str, str],
) -> dict[str, object]:
    model_secret_source = _single_secret_source(
        "--model-api-key",
        env_key=model_api_key_env,
        file_path=model_api_key_file,
    )
    kubernetes_api_token_source = _single_secret_source(
        "--kubernetes-api-token",
        env_key=kubernetes_api_token_env,
        file_path=kubernetes_api_token_file,
    )
    domain = kubernetes_profile_domain_config(
        domain_backend=domain_backend,
        kubectl_namespace=kubectl_namespace,
        kubectl_context=kubectl_context,
        kubectl_kubeconfig=kubectl_kubeconfig,
        kubectl_timeout_seconds=kubectl_timeout_seconds,
        kubernetes_api_server=kubernetes_api_server,
        kubernetes_api_namespace=kubernetes_api_namespace,
        kubernetes_api_token_secret=(
            kubernetes_api_token_secret if kubernetes_api_token_source is not None else None
        ),
        kubernetes_api_timeout_seconds=kubernetes_api_timeout_seconds,
    )
    store: dict[str, str] = {"backend": store_backend}
    if store_backend != "memory":
        store["path"] = store_path
    distributed_queue: dict[str, str] = {"backend": distributed_queue_backend}
    if distributed_queue_backend != "memory":
        distributed_queue["path"] = distributed_queue_path
    distributed_locks: dict[str, str] = {"backend": distributed_locks_backend}
    if distributed_locks_backend != "memory":
        distributed_locks["path"] = distributed_locks_path
    distributed_workers: dict[str, str] = {"backend": distributed_workers_backend}
    if distributed_workers_backend != "memory":
        distributed_workers["path"] = distributed_workers_path
    runtime: dict[str, object] = {
        "environment": {"environment": environment},
        "model": _profile_model_config(
            model_provider=model_provider,
            model_name=model_name,
            model_endpoint=model_endpoint,
            model_api_key_source=model_secret_source,
            model_api_key_secret=model_api_key_secret,
            model_timeout_seconds=model_timeout_seconds,
            model_response_format=model_response_format,
            model_headers=model_headers,
        ),
        "store": store,
        "distributed_queue": distributed_queue,
        "distributed_locks": distributed_locks,
        "distributed_workers": distributed_workers,
        "limits": {"max_iterations": 20, "max_recovery_steps": 8},
        "domain": domain,
    }
    secrets: dict[str, dict[str, object]] = {}
    if model_secret_source is not None:
        _add_secret(secrets, model_api_key_secret, model_secret_source)
    if kubernetes_api_token_source is not None:
        _add_secret(secrets, kubernetes_api_token_secret, kubernetes_api_token_source)
    if secrets:
        runtime["secrets"] = secrets
    if distributed_terminal_retention_seconds is not None:
        runtime["distributed_terminal_retention_seconds"] = distributed_terminal_retention_seconds
    return {
        "name": profile_name,
        "version": "0.1.0",
        "description": "Local Kubernetes profile",
        "domain": domain,
        "runtime": runtime,
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
