from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Coroutine, Iterable, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ConcurrentResult[T]:
    ok: bool
    value: T | None = None
    error: BaseException | None = None


class CancellableTaskGroup[T]:
    def __init__(self, *, cancel_on_error: bool = False) -> None:
        self._cancel_on_error = cancel_on_error

    async def run(
        self,
        coros: Iterable[Awaitable[T]],
        *,
        limit: int = 1,
    ) -> Sequence[ConcurrentResult[T]]:
        if limit < 1:
            _close_unstarted_awaitables(tuple(coros))
            raise ValueError("concurrency limit must be >= 1")

        awaitables = tuple(coros)
        sem = asyncio.Semaphore(limit)
        abort = asyncio.Event()
        tasks: list[asyncio.Task[ConcurrentResult[T]]] = []

        async def _guard(coro: Awaitable[T]) -> ConcurrentResult[T]:
            started = False
            try:
                async with sem:
                    if abort.is_set():
                        _close_unstarted_awaitable(coro)
                        return ConcurrentResult(ok=False, error=asyncio.CancelledError())
                    started = True
                    value = await coro
                    return ConcurrentResult(ok=True, value=value)
            except asyncio.CancelledError:
                if not started:
                    _close_unstarted_awaitable(coro)
                if abort.is_set():
                    return ConcurrentResult(ok=False, error=asyncio.CancelledError())
                raise
            except BaseException as exc:
                if self._cancel_on_error:
                    abort.set()
                    for pending in tasks:
                        if pending is not asyncio.current_task() and not pending.done():
                            pending.cancel()
                return ConcurrentResult(ok=False, error=exc)

        tasks = [asyncio.create_task(_guard(coro)) for coro in awaitables]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return [task.result() for task in tasks]


def _close_unstarted_awaitables(awaitables: Sequence[Awaitable[Any]]) -> None:
    for awaitable in awaitables:
        _close_unstarted_awaitable(awaitable)


def _close_unstarted_awaitable(awaitable: Awaitable[Any]) -> None:
    if isinstance(awaitable, Coroutine):
        awaitable.close()
