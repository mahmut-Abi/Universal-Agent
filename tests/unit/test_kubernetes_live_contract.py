from __future__ import annotations

from pathlib import Path

import pytest

from universal_agent.core import read_json_file
from universal_agent.domains.kubernetes import (
    KubernetesLiveContractArtifactError,
    kubernetes_live_contract_artifact,
    write_kubernetes_live_contract_artifact,
)
from universal_agent.security import scan_for_secrets


@pytest.mark.contract
def test_kubernetes_live_contract_artifact_redacts_sensitive_payload_values() -> None:
    artifact = kubernetes_live_contract_artifact(
        name="check",
        status=0,
        environment={
            "profile": "production-operator",
            "openai_api_key": "secret-key",
        },
        payload={
            "status": "ok",
            "model": {
                "api_key_secret": "openai_api_key",
                "model_total_tokens": 42,
            },
            "headers": {"authorization": "Bearer secret-token"},
            "contract": {"status": "ok"},
        },
    )

    assert artifact["kind"] == "KubernetesLiveContractArtifact"
    assert artifact["summary"] == {
        "contract_status": "ok",
        "exit_code": 0,
        "payload_status": "ok",
    }
    assert artifact["environment"] == {
        "openai_api_key": "<redacted>",
        "profile": "production-operator",
    }
    payload = artifact["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == {
        "api_key_secret": "<redacted>",
        "model_total_tokens": 42,
    }
    assert payload["headers"] == {"authorization": "<redacted>"}
    assert scan_for_secrets(artifact).passed is True


@pytest.mark.contract
def test_write_kubernetes_live_contract_artifact_persists_redacted_json(
    tmp_path: Path,
) -> None:
    result = write_kubernetes_live_contract_artifact(
        tmp_path,
        name="run",
        status=0,
        environment={"workload": "deployment/api"},
        payload={
            "status": "waiting",
            "contract": {"status": "attention"},
            "run": {"session": {"pending_action": {"capability": "scale_workload"}}},
        },
    )

    assert result.path == tmp_path / "kubernetes_live_run.json"
    assert read_json_file(result.path) == result.artifact


@pytest.mark.unit
def test_write_kubernetes_live_contract_artifact_rejects_unknown_names(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        KubernetesLiveContractArtifactError,
        match="artifact name must be 'check' or 'run'",
    ):
        write_kubernetes_live_contract_artifact(
            tmp_path,
            name="delete",
            status=0,
            payload={"status": "ok"},
        )
