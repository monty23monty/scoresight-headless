from __future__ import annotations

from scoresight.core.events import LatestValueBus


async def test_latest_value_bus_drops_stale_pending_events() -> None:
    bus = LatestValueBus[int]()
    async with bus.subscribe() as queue:
        await bus.publish(1)
        await bus.publish(2)
        await bus.publish(3)
        assert await queue.get() == 3
    assert bus.subscriber_count == 0
