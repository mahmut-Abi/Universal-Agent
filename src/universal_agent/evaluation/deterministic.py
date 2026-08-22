from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType

from universal_agent.core import runtime_primitives


@dataclass(slots=True)
class DeterministicClock:
    """Step a timezone-aware clock forward on every runtime timestamp request."""

    start: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    step: timedelta = timedelta(seconds=1)
    tick: int = 0

    def __post_init__(self) -> None:
        if self.start.tzinfo is None:
            raise ValueError("deterministic clock start must be timezone-aware")
        if self.step < timedelta(0):
            raise ValueError("deterministic clock step must be non-negative")

    def now(self) -> datetime:
        value = self.start + (self.step * self.tick)
        self.tick += 1
        return value


@dataclass(slots=True)
class DeterministicIdFactory:
    """Generate stable runtime IDs per prefix for deterministic tests."""

    width: int = 4
    counters: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.width < 1:
            raise ValueError("deterministic id width must be positive")

    def new_id(self, prefix: str) -> str:
        if not prefix.strip():
            raise ValueError("deterministic id prefix must not be empty")
        next_value = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = next_value
        return f"{prefix}-{next_value:0{self.width}d}"


@dataclass(slots=True)
class DeterministicRuntimeMode:
    """Temporarily install deterministic runtime primitives for evaluation tests."""

    clock: DeterministicClock = field(default_factory=DeterministicClock)
    ids: DeterministicIdFactory = field(default_factory=DeterministicIdFactory)
    _context: AbstractContextManager[None] | None = field(default=None, init=False)

    def __enter__(self) -> DeterministicRuntimeMode:
        if self._context is not None:
            raise RuntimeError("deterministic runtime mode is already active")
        self._context = runtime_primitives(clock=self.clock.now, id_factory=self.ids.new_id)
        self._context.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._context is None:
            return
        self._context.__exit__(exc_type, exc, traceback)
        self._context = None
