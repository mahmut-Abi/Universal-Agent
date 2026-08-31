from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable, Sequence
from dataclasses import dataclass


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
            raise ValueError("concurrency limit must be >= 1")

        sem = asyncio.Semaphore(limit)
        abort = asyncio.Event()
        tasks: list[asyncio.Task[ConcurrentResult[T]]] = []

        async def _guard(coro: Awaitable[T]) -> ConcurrentResult[T]:
            async with sem:
                if abort.is_set():
                    return ConcurrentResult(ok=False, error=asyncio.CancelledError())
                try:
                    value = await coro
                    return ConcurrentResult(ok=True, value=value)
                except asyncio.CancelledError:
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

        tasks = [asyncio.create_task(_guard(coro)) for coro in coros]
        await asyncio.gather(*tasks, return_exceptions=True)
        return [task.result() for task in tasks]
