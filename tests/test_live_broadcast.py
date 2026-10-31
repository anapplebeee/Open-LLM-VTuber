from __future__ import annotations

import unittest

from src.open_llm_vtuber.live.output_hub import LiveOutputHub


class FakeViewer:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.received: list[str] = []

    async def send_text(self, payload: str) -> None:
        if self.fail:
            raise RuntimeError("viewer closed")
        self.received.append(payload)


class LiveOutputHubTest(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_reaches_all_viewers(self):
        hub = LiveOutputHub()
        first = FakeViewer()
        second = FakeViewer()
        await hub.register(first)
        await hub.register(second)
        delivered = await hub.broadcast('{"type":"audio","audio":"x"}')
        self.assertEqual(delivered, 2)
        self.assertEqual(first.received, ['{"type":"audio","audio":"x"}'])
        self.assertEqual(second.received, first.received)
        self.assertEqual(hub.count, 2)

    async def test_failed_viewer_is_removed_without_stopping_others(self):
        hub = LiveOutputHub()
        dead = FakeViewer(fail=True)
        alive = FakeViewer()
        await hub.register(dead)
        await hub.register(alive)
        delivered = await hub.broadcast('{"type":"control","text":"x"}')
        self.assertEqual(delivered, 1)
        self.assertEqual(alive.received, ['{"type":"control","text":"x"}'])
        self.assertEqual(hub.count, 1)

    async def test_unregister_is_idempotent(self):
        hub = LiveOutputHub()
        viewer = FakeViewer()
        await hub.register(viewer)
        await hub.unregister(viewer)
        await hub.unregister(viewer)
        self.assertEqual(await hub.broadcast("{}"), 0)


if __name__ == "__main__":
    unittest.main()
