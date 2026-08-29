from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_container_image_uses_generic_agentd_entrypoint() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["agent"]' in dockerfile
    assert 'CMD ["serve", "--host", "0.0.0.0", "--port", "8765"]' in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "/ready" in dockerfile
    assert "--profile-config" not in dockerfile
    assert "kubernetes" not in dockerfile.lower()


def test_container_build_context_excludes_local_state_and_caches() -> None:
    ignored = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert ".git/" in ignored
    assert ".venv/" in ignored
    assert ".universal-agent/" in ignored
    assert ".tmp/" in ignored
    assert "__pycache__/" in ignored
    assert "*.egg-info/" in ignored
