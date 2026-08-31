from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, NewType
from uuid import uuid4

from universal_agent.core import utc_now

ProceduralPatternId = NewType("ProceduralPatternId", str)


def new_procedural_pattern_id() -> ProceduralPatternId:
    return ProceduralPatternId(f"proc-{uuid4()}")


@dataclass(frozen=True, slots=True)
class ProceduralPattern:
    """A reusable procedural pattern learned from successful executions."""

    id: ProceduralPatternId = field(default_factory=new_procedural_pattern_id)
    name: str = ""
    description: str = ""
    # The goal pattern this procedure applies to
    goal_pattern: str = ""
    # The sequence of capabilities/steps that form the procedure
    steps: tuple[str, ...] = ()
    # Arguments template for each step (capability -> argument template)
    step_arguments: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Domain this pattern belongs to
    domain: str = ""
    # Capability sequence
    capabilities: tuple[str, ...] = ()
    # Success rate (0-1)
    success_rate: float = 1.0
    # Number of successful executions
    execution_count: int = 0
    # Last successful execution
    last_executed_at: datetime | None = None
    # Tags for categorization
    tags: tuple[str, ...] = ()
    # Source domain
    domain_name: str = ""
    domain_version: str = ""
    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


class ProceduralMemory:
    """Stores and retrieves procedural patterns learned from successful executions."""

    def __init__(self) -> None:
        self._patterns: dict[ProceduralPatternId, ProceduralPattern] = {}

    def add_pattern(self, pattern: ProceduralPattern) -> bool:
        """Add a new procedural pattern. Returns False if already exists."""
        if pattern.id in self._patterns:
            return False
        self._patterns[pattern.id] = pattern
        return True

    def update_pattern(self, pattern: ProceduralPattern) -> bool:
        """Update an existing pattern. Returns False if not found."""
        if pattern.id not in self._patterns:
            return False
        updated = pattern.__class__(
            id=pattern.id,
            name=pattern.name,
            description=pattern.description,
            goal_pattern=pattern.goal_pattern,
            steps=pattern.steps,
            step_arguments=pattern.step_arguments,
            domain=pattern.domain,
            capabilities=pattern.capabilities,
            success_rate=pattern.success_rate,
            execution_count=pattern.execution_count,
            last_executed_at=pattern.last_executed_at,
            tags=pattern.tags,
            domain_name=pattern.domain_name,
            domain_version=pattern.domain_version,
            metadata=pattern.metadata,
            created_at=self._patterns[pattern.id].created_at,
            updated_at=utc_now(),
        )
        self._patterns[pattern.id] = updated
        return True

    def record_success(self, pattern_id: ProceduralPatternId) -> bool:
        """Record a successful execution of a pattern."""
        if pattern_id not in self._patterns:
            return False
        pattern = self._patterns[pattern_id]
        new_count = pattern.execution_count + 1
        updated = pattern.__class__(
            id=pattern.id,
            name=pattern.name,
            description=pattern.description,
            goal_pattern=pattern.goal_pattern,
            steps=pattern.steps,
            step_arguments=pattern.step_arguments,
            domain=pattern.domain,
            capabilities=pattern.capabilities,
            success_rate=min(1.0, pattern.success_rate + 0.01),  # Slight boost
            execution_count=new_count,
            last_executed_at=utc_now(),
            tags=pattern.tags,
            domain_name=pattern.domain_name,
            domain_version=pattern.domain_version,
            metadata=pattern.metadata,
            created_at=pattern.created_at,
            updated_at=utc_now(),
        )
        self._patterns[pattern_id] = updated
        return True

    def get_pattern(self, pattern_id: ProceduralPatternId) -> ProceduralPattern | None:
        return self._patterns.get(pattern_id)

    def find_patterns(
        self,
        goal_description: str = "",
        capabilities: tuple[str, ...] = (),
        domain: str = "",
        min_success_rate: float = 0.0,
        limit: int = 10,
    ) -> list[ProceduralPattern]:
        """Find matching procedural patterns."""
        results = []
        for pattern in self._patterns.values():
            if domain and pattern.domain_name != domain:
                continue
            if pattern.success_rate < min_success_rate:
                continue
            if capabilities and not any(c in pattern.capabilities for c in capabilities):
                continue
            if goal_description and goal_description.lower() not in pattern.goal_pattern.lower():
                continue
            results.append(pattern)

        # Sort by success_rate * execution_count (prefer proven patterns)
        results.sort(
            key=lambda p: p.success_rate * (1 + p.execution_count * 0.1),
            reverse=True,
        )
        return results[:limit]

    def get_all(self) -> tuple[ProceduralPattern, ...]:
        return tuple(self._patterns.values())

    def __len__(self) -> int:
        return len(self._patterns)
