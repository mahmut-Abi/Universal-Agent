from __future__ import annotations

import pytest

from universal_agent.core.polling import poll_async_result


@pytest.mark.asyncio
@pytest.mark.unit
async def test_poll_async_result_returns_ready_result_without_retry() -> None:
    calls = 0

    async def fetch() -> tuple[int, ...]:
        nonlocal calls
        calls += 1
        return (1,)

    result = await poll_async_result(
        fetch,
        retry_if=lambda value: not value,
        timeout_seconds=1.0,
        poll_interval_seconds=0.001,
    )

    assert result == (1,)
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_poll_async_result_retries_until_result_matches() -> None:
    calls = 0

    async def fetch() -> tuple[int, ...]:
        nonlocal calls
        calls += 1
        return (1,) if calls == 3 else ()

    result = await poll_async_result(
        fetch,
        retry_if=lambda value: not value,
        timeout_seconds=1.0,
        poll_interval_seconds=0.001,
    )

    assert result == (1,)
    assert calls == 3


@pytest.mark.asyncio
@pytest.mark.unit
async def test_poll_async_result_returns_last_result_after_timeout() -> None:
    calls = 0

    async def fetch() -> tuple[int, ...]:
        nonlocal calls
        calls += 1
        return ()

    result = await poll_async_result(
        fetch,
        retry_if=lambda value: not value,
        timeout_seconds=0.0,
        poll_interval_seconds=0.001,
    )

    assert result == ()
    assert calls == 1
