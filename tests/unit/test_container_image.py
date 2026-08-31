from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_container_image_uses_generic_agentd_entrypoint() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["sh", "-c"]' in dockerfile
    assert "agent init" in dockerfile
    assert "--profile-config /config/profile.json" in dockerfile
    assert "--host 0.0.0.0" in dockerfile
    assert "--port 8765" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "/ready" in dockerfile
    assert "kubernetes" not in dockerfile.lower()


@pytest.mark.unit
def test_container_image_installs_from_locked_runtime_dependencies() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY pyproject.toml uv.lock README.md ./" in dockerfile
    assert "uv sync --locked --no-dev --no-editable" in dockerfile
    assert "agent version >/tmp/agent-version.json" in dockerfile
    assert "agent health >/tmp/agent-health.json" in dockerfile
    assert "VIRTUAL_ENV=/app/.venv" in dockerfile
    assert 'PATH="/app/.venv/bin:$PATH"' in dockerfile
    assert "pip install" not in dockerfile


@pytest.mark.unit
def test_container_image_declares_production_runtime_metadata() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG IMAGE_VERSION=0.1.0" in dockerfile
    assert "ARG VCS_REF=unknown" in dockerfile
    assert "ARG BUILD_DATE=unknown" in dockerfile
    assert 'org.opencontainers.image.title="Universal Agent Runtime"' in dockerfile
    assert 'org.opencontainers.image.licenses="MIT"' in dockerfile
    assert "AGENT_DATA_DIR=/data" in dockerfile
    assert "AGENT_CONFIG_DIR=/config" in dockerfile
    assert "mkdir -p /data /config" in dockerfile
    assert "chown -R agent:agent /app /data /config" in dockerfile
    assert "USER agent" in dockerfile


@pytest.mark.unit
def test_container_build_context_excludes_local_state_and_caches() -> None:
    ignored = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert ".git/" in ignored
    assert ".venv/" in ignored
    assert ".universal-agent/" in ignored
    assert ".tmp/" in ignored
    assert "__pycache__/" in ignored
    assert "*.egg-info/" in ignored
    assert "docs/" in ignored
    assert "examples/" in ignored
    assert "tests/" in ignored
    assert ".github/" in ignored
    assert "*.log" in ignored
    assert ".coverage" in ignored
