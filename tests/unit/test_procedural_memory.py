from __future__ import annotations

from universal_agent.memory.procedural import ProceduralMemory, ProceduralPattern


def test_procedural_memory_add_and_get() -> None:
    pm = ProceduralMemory()
    pattern = ProceduralPattern(
        name="test pattern",
        description="test",
        goal_pattern="test goal",
        steps=("step1", "step2"),
        domain="test-domain",
        capabilities=("cap1", "cap2"),
    )
    assert pm.add_pattern(pattern)
    assert len(pm) == 1
    retrieved = pm.get_pattern(pattern.id)
    assert retrieved is not None
    assert retrieved.name == "test pattern"


def test_procedural_memory_duplicate_rejected() -> None:
    pm = ProceduralMemory()
    pattern = ProceduralPattern(name="test", goal_pattern="test")
    assert pm.add_pattern(pattern)
    assert not pm.add_pattern(pattern)
    assert len(pm) == 1


def test_procedural_memory_record_success() -> None:
    pm = ProceduralMemory()
    pattern = ProceduralPattern(
        name="test", goal_pattern="test", execution_count=0, success_rate=0.5
    )
    pm.add_pattern(pattern)
    assert pm.record_success(pattern.id)
    updated = pm.get_pattern(pattern.id)
    assert updated is not None
    assert updated.execution_count == 1
    assert updated.success_rate > 0.5
    assert updated.last_executed_at is not None


def test_procedural_memory_find_patterns() -> None:
    pm = ProceduralMemory()

    # Add patterns
    p1 = ProceduralPattern(
        name="k8s deploy",
        goal_pattern="deploy app",
        domain_name="kubernetes",
        capabilities=("deploy", "scale"),
        success_rate=0.9,
        execution_count=10,
    )
    p2 = ProceduralPattern(
        name="db backup",
        goal_pattern="backup database",
        domain_name="postgresql",
        capabilities=("backup",),
        success_rate=0.8,
        execution_count=5,
    )
    p3 = ProceduralPattern(
        name="failed pattern",
        goal_pattern="something",
        domain_name="test",
        capabilities=("fail",),
        success_rate=0.2,
        execution_count=1,
    )

    pm.add_pattern(p1)
    pm.add_pattern(p2)
    pm.add_pattern(p3)

    # Filter by domain
    k8s_patterns = pm.find_patterns(domain="kubernetes")
    assert len(k8s_patterns) == 1
    assert k8s_patterns[0].name == "k8s deploy"

    # Filter by min success rate
    high_success = pm.find_patterns(min_success_rate=0.5)
    assert len(high_success) == 2

    # Filter by capabilities
    deploy_patterns = pm.find_patterns(capabilities=("deploy",))
    assert len(deploy_patterns) == 1

    # Sort by success rate * execution count
    all_patterns = pm.find_patterns()
    assert len(all_patterns) == 3
    # p1 should be first (0.9 * 1.1 = 0.99 > 0.8 * 1.5 = 1.2? wait...)
    # Actually p2: 0.8 * 1.5 = 1.2, p1: 0.9 * 2.0 = 1.8
    assert all_patterns[0].name == "k8s deploy"


def test_procedural_memory_update() -> None:
    pm = ProceduralMemory()
    pattern = ProceduralPattern(name="test", goal_pattern="test")
    pm.add_pattern(pattern)

    updated_pattern = pattern.__class__(
        id=pattern.id,
        name="updated",
        goal_pattern="updated",
    )
    assert pm.update_pattern(updated_pattern)
    retrieved = pm.get_pattern(pattern.id)
    assert retrieved is not None
    assert retrieved.name == "updated"
