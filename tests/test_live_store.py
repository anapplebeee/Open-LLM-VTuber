from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from src.open_llm_vtuber.live.events import RoomEvent
from src.open_llm_vtuber.live.recap import SessionRecapWriter
from src.open_llm_vtuber.live.store import LiveStore


class LiveStoreTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db_path = Path("data") / f"test-live-{uuid.uuid4().hex}.db"
        self.store = LiveStore(self.db_path)
        self.session_id = "session-1"
        self.store.start_session(self.session_id, 1000)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self.db_path}{suffix}")
            if path.exists():
                path.unlink()

    def test_events_are_deduplicated_and_session_is_closed(self):
        event = RoomEvent(
            id="event-1",
            type="chat",
            platform="bilibili",
            room_id="room-1",
            received_at_ms=1000,
            user={"id": "u1", "name": "小蓝"},
            content="你好",
        )
        self.store.append_event(self.session_id, event)
        self.store.append_event(self.session_id, event)
        self.assertEqual(len(self.store.recent_events(self.session_id)), 1)
        self.store.append_chat(self.session_id, "assistant", "你好呀", 1100)
        self.assertEqual(self.store.recent_chat(self.session_id)[0]["content"], "你好呀")
        self.store.end_session(self.session_id, 2000)
        self.assertEqual(self.store.get_session(self.session_id)["status"], "ended")

    async def test_recap_writer_stores_summary_and_latest_recap(self):
        class FakeLLM:
            async def chat_completion(self, messages, system=None):
                yield "本场聊了吃饭和游戏，观众问下次什么时候播。"

        self.store.append_chat(self.session_id, "assistant", "今天吃鱼", 1100)
        writer = SessionRecapWriter(self.store, FakeLLM())
        summary = await writer.write(self.session_id)
        self.assertIn("吃饭", summary)
        self.assertEqual(self.store.latest_recap()["summary"], summary)

    def test_multiple_sessions_keep_events_separate(self):
        self.store.start_session("session-2", 2000)
        first = RoomEvent(
            id="same-id",
            type="chat",
            platform="bilibili",
            room_id="room-1",
            received_at_ms=1000,
            user={"name": "A"},
            content="first",
        )
        second = first.model_copy(update={"content": "second"})
        self.store.append_event("session-1", first)
        self.store.append_event("session-2", second)
        self.assertEqual(self.store.recent_events("session-1")[0]["content"], "first")
        self.assertEqual(self.store.recent_events("session-2")[0]["content"], "second")


if __name__ == "__main__":
    unittest.main()
