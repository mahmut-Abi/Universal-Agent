from __future__ import annotations

from universal_agent.core import immutable_json
from universal_agent.memory.preference import PreferenceMemory


def test_preference_memory_set_and_get() -> None:
    pm = PreferenceMemory()
    pref = pm.set_preference(
        key="default_region",
        value=immutable_json({"region": "us-east-1"}),
        domain="aws",
        source="explicit",
    )
    assert pref.key == "default_region"
    assert pref.value == {"region": "us-east-1"}
    assert pref.domain == "aws"
    assert pref.source == "explicit"

    retrieved = pm.get_preference("default_region", domain="aws")
    assert retrieved is not None
    assert retrieved.key == "default_region"
    assert retrieved.value == {"region": "us-east-1"}


def test_preference_memory_update_existing() -> None:
    pm = PreferenceMemory()
    pm.set_preference("editor", immutable_json({"name": "vim"}), domain="global")
    pm.set_preference("editor", immutable_json({"name": "vscode"}), domain="global")

    # Should update existing
    assert pm.get_preference("editor", domain="global") is not None
    retrieved = pm.get_preference("editor", domain="global")
    assert retrieved is not None
    assert retrieved.value == {"name": "vscode"}
    assert retrieved.confirmation_count == 1  # Incremented on update
    assert retrieved.confidence == 1.0  # Capped at 1.0


def test_preference_memory_domain_isolation() -> None:
    pm = PreferenceMemory()
    pm.set_preference("region", immutable_json({"region": "us-east-1"}), domain="aws")
    pm.set_preference("region", immutable_json({"region": "europe-west1"}), domain="gcp")

    aws_pref = pm.get_preference("region", domain="aws")
    gcp_pref = pm.get_preference("region", domain="gcp")
    global_pref = pm.get_preference("region", domain="")

    assert aws_pref is not None
    assert aws_pref.value == {"region": "us-east-1"}
    assert gcp_pref is not None
    assert gcp_pref.value == {"region": "europe-west1"}
    assert global_pref is None  # No global preference set


def test_preference_memory_delete() -> None:
    pm = PreferenceMemory()
    pm.set_preference("temp_setting", immutable_json({"value": "test"}), domain="test")
    assert pm.get_preference("temp_setting", domain="test") is not None

    deleted = pm.delete_preference("temp_setting", domain="test")
    assert deleted
    assert pm.get_preference("temp_setting", domain="test") is None

    # Deleting non-existent returns False
    assert not pm.delete_preference("nonexistent", domain="test")


def test_preference_memory_get_all() -> None:
    pm = PreferenceMemory()
    pm.set_preference("a", immutable_json({"v": 1}), domain="d1")
    pm.set_preference("b", immutable_json({"v": 2}), domain="d1")
    pm.set_preference("c", immutable_json({"v": 3}), domain="d2")

    all_prefs = pm.get_all()
    assert len(all_prefs) == 3

    d1_prefs = pm.get_all(domain="d1")
    assert len(d1_prefs) == 2

    d2_prefs = pm.get_all(domain="d2")
    assert len(d2_prefs) == 1


def test_preference_memory_tags() -> None:
    pm = PreferenceMemory()
    pm.set_preference("setting1", immutable_json({"v": 1}), domain="d1", tags=("ui", "editor"))
    pm.set_preference("setting2", immutable_json({"v": 2}), domain="d1", tags=("editor",))
    pm.set_preference("setting3", immutable_json({"v": 3}), domain="d2", tags=("ui",))

    ui_prefs = pm.get_by_tag("ui", domain="d1")
    assert len(ui_prefs) == 1
    assert ui_prefs[0].key == "setting1"

    all_ui = pm.get_by_tag("ui")
    assert len(all_ui) == 2
