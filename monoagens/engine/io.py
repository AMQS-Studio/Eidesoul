"""Bounded thread executor for the synchronous seams inside the engine (ADR-0017 §4.1).

The event loop renews the lease heartbeat; a synchronous DB query, an embedder call, or a
blocking memory call on that loop turns latency into a takeover. Everything that blocks is
submitted here and awaited, never executed inline in an `async def`.
"""


import asyncio
import functools
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

_executor: ThreadPoolExecutor | None = None
_workers = 8


def configure(workers: int) -> None:
    """Resize the executor at boot; an already running pool keeps its threads."""
    global _workers
    _workers = max(1, int(workers))


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=_workers, thread_name_prefix="monoagens-io"
        )
    return _executor


async def run[T](fn: Callable[..., T], *args, **kwargs) -> T:
    """One synchronous block on the bounded pool; cancellation never interrupts a commit."""
    loop = asyncio.get_running_loop()
    call = functools.partial(fn, *args, **kwargs)
    return await loop.run_in_executor(_pool(), call)


def submit(fn: Callable[..., Any], *args, **kwargs) -> asyncio.Future:
    """Schedule without awaiting; used where a fire-and-forget write is honest."""
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(_pool(), functools.partial(fn, *args, **kwargs))


def close() -> None:
    """Shutdown hook: no new work after the shutdown phases complete."""
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None
