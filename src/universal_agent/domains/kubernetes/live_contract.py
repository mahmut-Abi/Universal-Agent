from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from universal_agent.core import JsonMapping, JsonValue, immutable_json, utc_now, write_json_file
from universal_agent.security import redact_sensitive_value, scan_for_secrets

LIVE_CONTRACT_ARTIFACT_API_VERSION = "agent.nantian.dev/v1alpha1"
LIVE_CONTRACT_ARTIFACT_KIND = "KubernetesLiveContractArtifact"


class KubernetesLiveContractArtifactError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class KubernetesLiveContractArtifactWrite:
    path: Path
    artifact: JsonMapping


def write_kubernetes_live_contract_artifact(
    output_dir: str | Path,
    *,
    name: str,
    status: int,
    payload: JsonMapping,
    environment: JsonMapping | None = None,
) -> KubernetesLiveContractArtifactWrite:
    """Write a redacted live Kubernetes contract artifact.

    The artifact records the CLI contract payload and non-secret execution
    metadata. It is intentionally independent from pytest and CI so local
    operators, live tests and future pipelines can share one sanitizing seam.
    """

    artifact = kubernetes_live_contract_artifact(
        name=name,
        status=status,
        payload=payload,
        environment=environment,
    )
    report = scan_for_secrets(artifact)
    if not report.passed:
        paths = ", ".join(report.paths)
        raise KubernetesLiveContractArtifactError(
            f"refusing to write Kubernetes live contract artifact with secrets: {paths}"
        )
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{_artifact_file_stem(name)}.json"
    write_json_file(path, artifact, indent=True)
    return KubernetesLiveContractArtifactWrite(path, artifact)


def kubernetes_live_contract_artifact(
    *,
    name: str,
    status: int,
    payload: JsonMapping,
    environment: JsonMapping | None = None,
) -> JsonMapping:
    artifact_name = _artifact_name(name)
    sanitized_payload = _redact_json_value(dict(payload))
    sanitized_environment = _redact_json_value(dict(environment or {}))
    if not isinstance(sanitized_payload, dict):
        raise KubernetesLiveContractArtifactError("Kubernetes contract payload must be an object")
    if not isinstance(sanitized_environment, dict):
        raise KubernetesLiveContractArtifactError(
            "Kubernetes contract environment metadata must be an object"
        )
    return immutable_json(
        {
            "apiVersion": LIVE_CONTRACT_ARTIFACT_API_VERSION,
            "kind": LIVE_CONTRACT_ARTIFACT_KIND,
            "metadata": {
                "name": artifact_name,
                "generated_at": utc_now().isoformat(),
            },
            "summary": {
                "exit_code": status,
                "payload_status": _payload_status(sanitized_payload),
                "contract_status": _contract_status(sanitized_payload),
            },
            "environment": sanitized_environment,
            "payload": sanitized_payload,
        }
    )


def _redact_json_value(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {
            str(key): redact_sensitive_value(
                str(key),
                _redact_json_value(item),
                replacement="<redacted>",
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json_value(item) for item in value]
    return value


def _payload_status(payload: Mapping[str, JsonValue]) -> str:
    status = payload.get("status")
    return status if isinstance(status, str) else ""


def _contract_status(payload: Mapping[str, JsonValue]) -> str:
    contract = payload.get("contract")
    if not isinstance(contract, Mapping):
        return ""
    status = contract.get("status")
    return status if isinstance(status, str) else ""


def _artifact_name(name: str) -> str:
    normalized = name.strip().lower().replace("_", "-")
    if normalized not in {"check", "run"}:
        raise KubernetesLiveContractArtifactError(
            "Kubernetes live contract artifact name must be 'check' or 'run'"
        )
    return f"kubernetes-live-{normalized}"


def _artifact_file_stem(name: str) -> str:
    return _artifact_name(name).replace("-", "_")
