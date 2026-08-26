from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent import (
    DomainConfig,
    ModelConfig,
    ModelProvider,
    RuntimeConfig,
    RuntimeLimitsConfig,
    SecretRef,
    SecretSource,
    StoreBackend,
    StoreConfig,
)


def test_runtime_config_from_mapping_parses_typed_values() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "environment": {"environment": "production"},
            "store": {"backend": "file", "path": "/tmp/universal-agent"},
            "distributed_queue": {
                "backend": "file",
                "path": "/tmp/universal-agent/work-queue.json",
            },
            "distributed_locks": {
                "backend": "file",
                "path": "/tmp/universal-agent/distributed-locks.json",
            },
            "distributed_workers": {
                "backend": "file",
                "path": "/tmp/universal-agent/workers.json",
            },
            "distributed_terminal_retention_seconds": 3600,
            "limits": {"max_iterations": 7, "max_recovery_steps": 3},
            "domain": {"name": "kubernetes", "version": "0.2.0"},
        }
    )

    assert config.environment["environment"] == "production"
    assert config.secrets == ()
    assert config.store == StoreConfig.file("/tmp/universal-agent")
    assert config.distributed_queue == StoreConfig.file("/tmp/universal-agent/work-queue.json")
    assert config.distributed_locks == StoreConfig.file(
        "/tmp/universal-agent/distributed-locks.json"
    )
    assert config.distributed_workers == StoreConfig.file("/tmp/universal-agent/workers.json")
    assert config.distributed_terminal_retention_seconds == 3600.0
    assert config.store.backend is StoreBackend.FILE
    assert config.limits == RuntimeLimitsConfig(max_iterations=7, max_recovery_steps=3)
    assert config.domain == DomainConfig("kubernetes", "0.2.0")
    assert config.configured_domains() == (DomainConfig("kubernetes", "0.2.0"),)


def test_runtime_config_from_mapping_parses_secret_refs() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "secrets": {
                "openai_api_key": {
                    "source": "env",
                    "key": "OPENAI_API_KEY",
                    "required": True,
                },
                "optional_token": {
                    "source": "env",
                    "key": "OPTIONAL_TOKEN",
                    "required": False,
                },
            }
        }
    )

    assert config.secrets == (
        SecretRef("openai_api_key", SecretSource.ENV, "OPENAI_API_KEY", True),
        SecretRef("optional_token", SecretSource.ENV, "OPTIONAL_TOKEN", False),
    )


def test_runtime_config_from_mapping_parses_file_secret_refs() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "secrets": {
                "model_api_key": {
                    "source": "file",
                    "key": "/run/secrets/model-api-key",
                    "required": True,
                }
            }
        }
    )

    assert config.secrets == (
        SecretRef("model_api_key", SecretSource.FILE, "/run/secrets/model-api-key", True),
    )
    assert config.secrets[0] == SecretRef.file(
        "model_api_key",
        "/run/secrets/model-api-key",
    )


def test_runtime_config_rejects_invalid_secret_refs() -> None:
    with pytest.raises(ValueError, match="secrets must be an object"):
        RuntimeConfig.from_mapping({"secrets": []})

    with pytest.raises(ValueError, match="key must be a string"):
        RuntimeConfig.from_mapping({"secrets": {"api_key": {"source": "env"}}})

    with pytest.raises(ValueError, match="required must be a boolean"):
        RuntimeConfig.from_mapping(
            {"secrets": {"api_key": {"source": "env", "key": "API_KEY", "required": "yes"}}}
        )

    with pytest.raises(ValueError, match="duplicate runtime secrets"):
        RuntimeConfig(
            secrets=(
                SecretRef.env("api_key", "API_KEY"),
                SecretRef.env("api_key", "API_KEY_2"),
            )
        ).validate()


def test_runtime_config_from_mapping_parses_json_http_model_config() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "secrets": {
                "openai_api_key": {
                    "source": "env",
                    "key": "OPENAI_API_KEY",
                    "required": True,
                }
            },
            "model": {
                "provider": "json_http",
                "name": "runtime-decider",
                "endpoint": "https://models.example.test/decide",
                "api_key_secret": "openai_api_key",
                "timeout_seconds": 4.5,
                "headers": {"X-Agent-Runtime": "test"},
            },
        }
    )

    assert config.model == ModelConfig.json_http(
        name="runtime-decider",
        endpoint="https://models.example.test/decide",
        api_key_secret="openai_api_key",
        timeout_seconds=4.5,
        headers={"X-Agent-Runtime": "test"},
    )
    assert config.model.provider is ModelProvider.JSON_HTTP


def test_runtime_config_from_mapping_parses_openai_chat_completions_model_config() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "secrets": {
                "openai_api_key": {
                    "source": "env",
                    "key": "OPENAI_API_KEY",
                    "required": True,
                }
            },
            "model": {
                "provider": "openai_chat_completions",
                "name": "gpt-runtime",
                "api_key_secret": "openai_api_key",
                "timeout_seconds": 4.5,
                "headers": {"OpenAI-Organization": "org-test"},
                "response_format": "json_object",
            },
        }
    )

    assert config.model == ModelConfig.openai_chat_completions(
        name="gpt-runtime",
        api_key_secret="openai_api_key",
        timeout_seconds=4.5,
        headers={"OpenAI-Organization": "org-test"},
        response_format="json_object",
    )
    assert config.model.provider is ModelProvider.OPENAI_CHAT_COMPLETIONS


def test_runtime_config_from_mapping_parses_openai_chat_prompt_json_model_config() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "secrets": {
                "openai_api_key": {
                    "source": "env",
                    "key": "OPENAI_API_KEY",
                    "required": True,
                }
            },
            "model": {
                "provider": "openai_chat_completions",
                "name": "gpt-runtime",
                "api_key_secret": "openai_api_key",
                "response_format": "prompt_json",
            },
        }
    )

    assert config.model == ModelConfig.openai_chat_completions(
        name="gpt-runtime",
        api_key_secret="openai_api_key",
        response_format="prompt_json",
    )


def test_runtime_config_from_mapping_parses_openai_responses_model_config() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "secrets": {
                "openai_api_key": {
                    "source": "env",
                    "key": "OPENAI_API_KEY",
                    "required": True,
                }
            },
            "model": {
                "provider": "openai_responses",
                "name": "gpt-runtime",
                "api_key_secret": "openai_api_key",
                "timeout_seconds": 4.5,
                "headers": {"OpenAI-Organization": "org-test"},
            },
        }
    )

    assert config.model == ModelConfig.openai_responses(
        name="gpt-runtime",
        api_key_secret="openai_api_key",
        timeout_seconds=4.5,
        headers={"OpenAI-Organization": "org-test"},
    )
    assert config.model.provider is ModelProvider.OPENAI_RESPONSES


def test_runtime_config_rejects_invalid_model_config() -> None:
    with pytest.raises(ValueError, match="json_http model requires endpoint"):
        RuntimeConfig.from_mapping({"model": {"provider": "json_http", "name": "runtime"}})

    with pytest.raises(ValueError, match="model api_key_secret is not declared"):
        RuntimeConfig.from_mapping(
            {
                "model": {
                    "provider": "json_http",
                    "name": "runtime",
                    "endpoint": "https://models.example.test/decide",
                    "api_key_secret": "missing",
                }
            }
        )

    with pytest.raises(ValueError, match=r"headers\.X-Test must be a string"):
        RuntimeConfig.from_mapping(
            {
                "model": {
                    "provider": "json_http",
                    "name": "runtime",
                    "endpoint": "https://models.example.test/decide",
                    "headers": {"X-Test": 123},
                }
            }
        )

    with pytest.raises(ValueError, match="openai_responses model requires api_key_secret"):
        RuntimeConfig.from_mapping(
            {"model": {"provider": "openai_responses", "name": "gpt-runtime"}}
        )

    with pytest.raises(ValueError, match="openai_chat_completions model requires api_key_secret"):
        RuntimeConfig.from_mapping(
            {"model": {"provider": "openai_chat_completions", "name": "gpt-runtime"}}
        )

    with pytest.raises(ValueError, match="openai_chat_completions response_format"):
        RuntimeConfig.from_mapping(
            {
                "secrets": {"openai_api_key": {"source": "env", "key": "OPENAI_API_KEY"}},
                "model": {
                    "provider": "openai_chat_completions",
                    "name": "gpt-runtime",
                    "api_key_secret": "openai_api_key",
                    "response_format": "text",
                },
            }
        )

    with pytest.raises(ValueError, match="openai_responses model does not accept response_format"):
        RuntimeConfig.from_mapping(
            {
                "secrets": {"openai_api_key": {"source": "env", "key": "OPENAI_API_KEY"}},
                "model": {
                    "provider": "openai_responses",
                    "name": "gpt-runtime",
                    "api_key_secret": "openai_api_key",
                    "response_format": "json_object",
                },
            }
        )


def test_runtime_config_from_mapping_parses_multi_domain_values() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "domains": [
                {"name": "kubernetes", "version": "0.2.0"},
                {"name": "observability", "version": "0.1.0"},
            ],
        }
    )

    assert config.domain == DomainConfig("kubernetes", "0.2.0")
    assert config.configured_domains() == (
        DomainConfig("kubernetes", "0.2.0"),
        DomainConfig("observability", "0.1.0"),
    )


def test_runtime_config_from_mapping_parses_domain_backend_settings() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "domain": {
                "name": "kubernetes",
                "version": "0.2.0",
                "backend": "kubectl",
                "settings": {
                    "default_namespace": "prod",
                    "context": "prod-cluster",
                    "kubeconfig": "/tmp/kubeconfig",
                    "timeout_seconds": 3.5,
                },
            },
        }
    )

    assert config.domain.backend == "kubectl"
    assert config.domain.settings == {
        "default_namespace": "prod",
        "context": "prod-cluster",
        "kubeconfig": "/tmp/kubeconfig",
        "timeout_seconds": 3.5,
    }
    assert config.domain.identity().name == "kubernetes"


def test_runtime_config_from_json_file_parses_typed_values(tmp_path: Path) -> None:
    path = tmp_path / "runtime-config.json"
    path.write_text(
        """
        {
          "environment": {"environment": "production"},
          "store": {"backend": "file", "path": "/tmp/universal-agent"},
          "limits": {"max_iterations": 7, "max_recovery_steps": 3},
          "domain": {"name": "kubernetes", "version": "0.2.0"}
        }
        """,
        encoding="utf-8",
    )

    config = RuntimeConfig.from_json_file(path)

    assert config.environment["environment"] == "production"
    assert config.secrets == ()
    assert config.store == StoreConfig.file("/tmp/universal-agent")
    assert config.limits == RuntimeLimitsConfig(max_iterations=7, max_recovery_steps=3)
    assert config.domain == DomainConfig("kubernetes", "0.2.0")


def test_runtime_config_parses_sqlite_store() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "store": {"backend": "sqlite", "path": "/tmp/universal-agent/runtime.sqlite3"},
        }
    )

    assert config.store == StoreConfig.sqlite("/tmp/universal-agent/runtime.sqlite3")
    assert config.store.backend is StoreBackend.SQLITE


def test_runtime_config_parses_sqlite_distributed_locks() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "distributed_locks": {
                "backend": "sqlite",
                "path": "/tmp/universal-agent/distributed-locks.sqlite3",
            },
        }
    )

    assert config.distributed_locks == StoreConfig.sqlite(
        "/tmp/universal-agent/distributed-locks.sqlite3"
    )
    assert config.distributed_locks.backend is StoreBackend.SQLITE


def test_runtime_config_parses_sqlite_distributed_queue() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "distributed_queue": {
                "backend": "sqlite",
                "path": "/tmp/universal-agent/work-queue.sqlite3",
            },
        }
    )

    assert config.distributed_queue == StoreConfig.sqlite("/tmp/universal-agent/work-queue.sqlite3")
    assert config.distributed_queue.backend is StoreBackend.SQLITE


def test_runtime_config_parses_sqlite_distributed_workers() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "distributed_workers": {
                "backend": "sqlite",
                "path": "/tmp/universal-agent/workers.sqlite3",
            },
        }
    )

    assert config.distributed_workers == StoreConfig.sqlite("/tmp/universal-agent/workers.sqlite3")
    assert config.distributed_workers.backend is StoreBackend.SQLITE


def test_runtime_config_rejects_invalid_store_and_limits() -> None:
    with pytest.raises(ValueError, match="file store requires path"):
        StoreConfig.from_mapping({"backend": "file"})

    with pytest.raises(ValueError, match="memory store does not accept path"):
        StoreConfig.from_mapping({"backend": "memory", "path": "/tmp/runtime"})

    with pytest.raises(ValueError, match="sqlite store requires path"):
        StoreConfig.from_mapping({"backend": "sqlite"})

    with pytest.raises(ValueError, match="max_iterations must be positive"):
        RuntimeLimitsConfig(max_iterations=0).validate()

    with pytest.raises(ValueError, match="environment must be an object"):
        RuntimeConfig.from_mapping({"environment": "production"})

    with pytest.raises(ValueError, match="distributed_terminal_retention_seconds must be positive"):
        RuntimeConfig.from_mapping({"distributed_terminal_retention_seconds": 0})

    with pytest.raises(ValueError, match="duplicate configured domains"):
        RuntimeConfig.from_mapping(
            {
                "domains": [
                    {"name": "kubernetes", "version": "0.2.0"},
                    {"name": "kubernetes", "version": "0.2.0"},
                ]
            }
        )


def test_runtime_config_rejects_non_object_json_file(tmp_path: Path) -> None:
    path = tmp_path / "runtime-config.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(ValueError, match="runtime config file must be an object"):
        RuntimeConfig.from_json_file(path)
