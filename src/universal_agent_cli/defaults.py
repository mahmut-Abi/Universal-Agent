from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

AGENT_CONFIG_DIR_ENV = "AGENT_CONFIG_DIR"
AGENT_DATA_DIR_ENV = "AGENT_DATA_DIR"
DEFAULT_AGENT_DATA_DIR = ".universal-agent"
DEFAULT_PROFILE_CONFIG_NAME = "profile.json"
DEFAULT_STORE_PATH_NAME = "store"
DEFAULT_WORK_QUEUE_PATH_NAME = "work-queue.json"
DEFAULT_DISTRIBUTED_LOCKS_PATH_NAME = "distributed-locks.json"
DEFAULT_WORKERS_PATH_NAME = "workers.json"

__all__ = [
    "AGENT_CONFIG_DIR_ENV",
    "AGENT_DATA_DIR_ENV",
    "DEFAULT_AGENT_DATA_DIR",
    "DEFAULT_DISTRIBUTED_LOCKS_PATH_NAME",
    "DEFAULT_PROFILE_CONFIG_NAME",
    "DEFAULT_STORE_PATH_NAME",
    "DEFAULT_WORKERS_PATH_NAME",
    "DEFAULT_WORK_QUEUE_PATH_NAME",
    "default_distributed_locks_path",
    "default_init_output_path",
    "default_runtime_data_dir",
    "default_store_path",
    "default_work_queue_path",
    "default_workers_path",
    "global_init_output_path",
]


def default_init_output_path(environ: Mapping[str, str] | None = None) -> str:
    """Golden Path init target: AGENT_CONFIG_DIR/profile.json, else
    ./universal-agent/profile.json.

    Runtime discovery may fall back to ``~/.universal-agent/profile.json`` for
    existing user-level configs, but ``agent init`` itself creates project-local
    config unless the caller explicitly opts into global config.
    """

    env = environ or os.environ
    config_dir = env.get(AGENT_CONFIG_DIR_ENV, "").strip()
    if config_dir:
        return str(Path(config_dir) / DEFAULT_PROFILE_CONFIG_NAME)
    return str(Path("universal-agent") / DEFAULT_PROFILE_CONFIG_NAME)


def global_init_output_path(environ: Mapping[str, str] | None = None) -> str:
    """Explicit user-level init target used by ``agent init --global``."""

    env = environ or os.environ
    home = env.get("HOME", "").strip()
    if home:
        return str(Path(home) / ".universal-agent" / DEFAULT_PROFILE_CONFIG_NAME)
    return str(Path(".universal-agent") / DEFAULT_PROFILE_CONFIG_NAME)


def default_runtime_data_dir(environ: Mapping[str, str] | None = None) -> str:
    data_dir = _optional_env_path(AGENT_DATA_DIR_ENV, environ)
    if data_dir is None:
        return DEFAULT_AGENT_DATA_DIR
    return str(data_dir)


def default_store_path(environ: Mapping[str, str] | None = None) -> str:
    return _runtime_data_path(DEFAULT_STORE_PATH_NAME, environ)


def default_work_queue_path(environ: Mapping[str, str] | None = None) -> str:
    return _runtime_data_path(DEFAULT_WORK_QUEUE_PATH_NAME, environ)


def default_distributed_locks_path(environ: Mapping[str, str] | None = None) -> str:
    return _runtime_data_path(DEFAULT_DISTRIBUTED_LOCKS_PATH_NAME, environ)


def default_workers_path(environ: Mapping[str, str] | None = None) -> str:
    return _runtime_data_path(DEFAULT_WORKERS_PATH_NAME, environ)


def _runtime_data_path(name: str, environ: Mapping[str, str] | None) -> str:
    return str(Path(default_runtime_data_dir(environ)) / name)


def _optional_env_path(name: str, environ: Mapping[str, str] | None) -> Path | None:
    value = (environ or os.environ).get(name)
    if value is None or not value.strip():
        return None
    return Path(value)
