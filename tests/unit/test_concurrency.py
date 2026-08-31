from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import pytest

from universal_agent.coordination.concurrency import (
    CancellableTaskGroup,
    ConcurrentResult,
)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_concurrency_limit_is_enforced() -> None:
    started = 0
    peak = 0
    lock = asyncio.Lock()

    async def work() -> int:
        nonlocal started, peak
        async with lock:
            started += 1
            peak = max(peak, started)
        await asyncio.sleep(0.05)
        async with lock:
            started -= 1
        return 1

    group = CancellableTaskGroup[int]()
    coros = [work() for _ in range(10)]
    results = await group.run(coros, limit=3)

    assert len(results) == 10
    assert peak <= 3
    assert all(r.ok for r in results)
    assert all(r.value == 1 for r in results)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_successful_results_collected() -> None:
    async def double(n: int) -> int:
        await asyncio.sleep(0.01)
        return n * 2

    group = CancellableTaskGroup[int]()
    results = await group.run([double(n) for n in range(5)], limit=2)

    assert [r.value for r in results] == [0, 2, 4, 6, 8]
    assert all(r.ok for r in results)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_partial_failure_is_isolated() -> None:
    completed: list[int] = []

    async def maybe_fail(n: int) -> int:
        await asyncio.sleep(0.01)
        if n == 2:
            raise ValueError("boom")
        completed.append(n)
        return n

    group = CancellableTaskGroup[int]()
    results = await group.run([maybe_fail(n) for n in range(5)], limit=5)

    assert results[2].ok is False
    assert isinstance(results[2].error, ValueError)
    assert results[2].value is None
    assert all(r.ok for i, r in enumerate(results) if i != 2)
    assert sorted(completed) == [0, 1, 3, 4]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancel_on_error_cancels_siblings() -> None:
    finished: list[int] = []

    async def slow(n: int) -> int:
        try:
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        finished.append(n)
        return n

    async def fail() -> int:
        await asyncio.sleep(0.01)
        raise RuntimeError("stop")

    group = CancellableTaskGroup[int](cancel_on_error=True)
    coros: list[Awaitable[int]] = [slow(n) for n in range(4)]
    coros.insert(0, fail())
    results = await group.run(coros, limit=5)

    assert results[0].ok is False
    assert isinstance(results[0].error, RuntimeError)
    assert all(not r.ok for r in results[1:])
    assert finished == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_external_cancellation_propagates() -> None:
    async def work() -> int:
        await asyncio.sleep(10)
        return 1

    group = CancellableTaskGroup[int]()

    pending = [asyncio.create_task(group.run([work() for _ in range(3)], limit=2))]
    await asyncio.sleep(0.01)
    pending[0].cancel()

    with pytest.raises(asyncio.CancelledError):
        await pending[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_limit_rejected() -> None:
    async def noop() -> int:
        return 0

    group = CancellableTaskGroup[int]()
    with pytest.raises(ValueError):
        await group.run([noop() for _ in range(1)], limit=0)


def test_concurrent_result_dataclass() -> None:
    ok = ConcurrentResult[int](ok=True, value=5)
    bad = ConcurrentResult[int](ok=False, error=RuntimeError("x"))

    assert ok.ok is True
    assert ok.value == 5
    assert ok.error is None
    assert bad.ok is False
    assert bad.value is None
    assert isinstance(bad.error, RuntimeError)
