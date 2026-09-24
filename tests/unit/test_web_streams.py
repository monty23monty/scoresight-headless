from __future__ import annotations

import asyncio

import pytest
from fastapi import WebSocketDisconnect

from scoresight.core.events import LatestValueBus
from scoresight.web.app import _run_socket_stream


async def test_idle_socket_disconnect_releases_subscription():
    disconnected = asyncio.Event()
    subscribed = asyncio.Event()
    bus = LatestValueBus()

    class Socket:
        async def receive_text(self):
            await disconnected.wait()
            raise WebSocketDisconnect()

    async def send():
        async with bus.subscribe() as queue:
            subscribed.set()
            await queue.get()

    task = asyncio.create_task(_run_socket_stream(Socket(), send))
    await subscribed.wait()
    assert bus.subscriber_count == 1
    disconnected.set()
    with pytest.raises(WebSocketDisconnect):
        await asyncio.wait_for(task, timeout=1)
    assert bus.subscriber_count == 0
