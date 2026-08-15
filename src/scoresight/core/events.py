from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Generic, TypeVar

T = TypeVar("T")


class LatestValueBus(Generic[T]):
    """Fan-out bus that keeps only the latest pending event per subscriber."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[T]] = set()
        self._lock = asyncio.Lock()

    async def publish(self, event: T) -> None:
        async with self._lock:
            subscribers = tuple(self._subscribers)
        for queue in subscribers:
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[T]]:
        queue: asyncio.Queue[T] = asyncio.Queue(maxsize=1)
        async with self._lock:
            self._subscribers.add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
