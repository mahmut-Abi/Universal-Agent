from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from tenacity import AsyncRetrying, RetryCallState, retry_if_result, stop_after_delay
from tenacity.wait import wait_base, wait_fixed


async def poll_async_result[T](
    fetch: Callable[[], Awaitable[T]],
    *,
    retry_if: Callable[[T], bool],
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> T:
    """Poll an async producer until its result no longer needs retrying."""

    retrying = AsyncRetrying(
        stop=stop_after_delay(timeout_seconds),
        wait=_CappedWait(wait_fixed(poll_interval_seconds), timeout_seconds),
        retry=retry_if_result(retry_if),
        retry_error_callback=_last_result,
    )

    async def fetch_result() -> T:
        return await fetch()

    return cast(T, await retrying(fetch_result))


class _CappedWait(wait_base):
    def __init__(self, wait: wait_base, timeout_seconds: float) -> None:
        self._wait = wait
        self._timeout_seconds = timeout_seconds

    def __call__(self, retry_state: RetryCallState) -> float:
        delay = self._wait(retry_state)
        if retry_state.seconds_since_start is None:
            return delay
        remaining = max(0.0, self._timeout_seconds - retry_state.seconds_since_start)
        return min(delay, remaining)


def _last_result(retry_state: RetryCallState) -> object:
    if retry_state.outcome is None:
        raise RuntimeError("polling stopped without a result")
    return retry_state.outcome.result()
