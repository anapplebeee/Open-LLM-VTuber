from __future__ import annotations

import unittest

from src.open_llm_vtuber.dashboard import _resources
from src.open_llm_vtuber.live.registry import (
    get_current_live_session,
    set_current_live_session,
)
from src.open_llm_vtuber.live.runtime import LiveSession


class DashboardTest(unittest.IsolatedAsyncioTestCase):
    async def test_song_control_uses_existing_output(self):
        events = []

        async def on_song(payload):
            events.append(payload)

        session = LiveSession(agent=None, on_output=lambda _: None, on_song=on_song)
        await session.control_song("next")
        self.assertEqual(events, [{"type": "song-control", "action": "next"}])

    async def test_song_control_rejects_unknown_action(self):
        session = LiveSession(agent=None, on_output=lambda _: None)
        with self.assertRaises(ValueError):
            await session.control_song("explode")

    def test_registry(self):
        marker = object()
        set_current_live_session(marker)
        self.assertIs(get_current_live_session(), marker)
        set_current_live_session(None)
        self.assertIsNone(get_current_live_session())

    def test_resources_is_json_shaped(self):
        data = _resources()
        self.assertIn("available", data)
