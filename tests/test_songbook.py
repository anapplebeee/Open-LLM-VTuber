from __future__ import annotations

import unittest

from src.open_llm_vtuber.live.events import RoomEvent
from src.open_llm_vtuber.live.runtime import LiveSession
from src.open_llm_vtuber.live.songbook import SongRequestQueue


def event(text: str, *, user: str = "u1", at: int = 1_000_000) -> RoomEvent:
    return RoomEvent(
        id=f"e-{at}",
        type="chat",
        platform="test",
        received_at_ms=at,
        user={"id": user, "name": user},
        content=text,
    )


class SongRequestQueueTest(unittest.TestCase):
    def test_point_song_is_queued_with_position(self):
        queue = SongRequestQueue()
        result = queue.handle(event("点歌 海底", at=1_000_000))
        self.assertIsNotNone(result)
        self.assertEqual(result.action, "queued")
        self.assertEqual(result.position, 1)
        self.assertIn("第 1 位", result.reply_hint)
        self.assertEqual(queue.snapshot()["items"][0]["title"], "海底")

    def test_list_and_cancel(self):
        queue = SongRequestQueue()
        self.assertEqual(queue.handle(event("点歌 海底")).action, "queued")
        listed = queue.handle(event("点歌列表", user="u2"))
        self.assertEqual(listed.action, "listed")
        self.assertIn("海底", listed.reply_hint)
        cancelled = queue.handle(event("取消点歌 海底"))
        self.assertEqual(cancelled.action, "cancelled")
        self.assertEqual(queue.snapshot()["items"], [])

    def test_cooldown_and_full_queue(self):
        queue = SongRequestQueue(cooldown_ms=60_000, max_queue=2)
        self.assertEqual(queue.handle(event("点歌 A", at=1_000_000)).action, "queued")
        self.assertEqual(queue.handle(event("点歌 B", at=1_000_010)).action, "rejected")
        self.assertEqual(
            queue.handle(event("点歌 C", user="u2", at=1_000_020)).action,
            "queued",
        )
        full = queue.handle(event("点歌 D", user="u3", at=1_000_030))
        self.assertEqual(full.action, "rejected")
        self.assertIn("满了", full.reply_hint)

    def test_duplicate_and_url_are_rejected(self):
        queue = SongRequestQueue()
        self.assertEqual(queue.handle(event("点歌 海底")).action, "queued")
        duplicate = queue.handle(event("点歌 海底", user="u2", at=1_000_100))
        self.assertEqual(duplicate.action, "rejected")
        self.assertIn("已经在歌单", duplicate.reply_hint)
        url = queue.handle(event("点歌 https://example.com/a.mp3", user="u3", at=1_000_200))
        self.assertEqual(url.action, "rejected")

    def test_only_self_can_cancel(self):
        queue = SongRequestQueue()
        queue.handle(event("点歌 海底", user="u1"))
        result = queue.handle(event("取消点歌 海底", user="u2", at=1_000_100))
        self.assertEqual(result.action, "rejected")
        self.assertIn("只能取消", result.reply_hint)


class LiveSongIntegrationTest(unittest.TestCase):
    def test_song_command_is_enriched_before_director(self):
        session = LiveSession(agent=None, on_output=lambda _: None)
        enriched = session._with_song_request(event("点歌 海底"))
        self.assertIn("点歌 海底", enriched.content)
        self.assertIn("【点歌系统】", enriched.content)
        self.assertIn("第 1 位", enriched.content)
        self.assertEqual(session.status()["song_requests"]["items"][0]["title"], "海底")

    def test_non_song_chat_is_unchanged(self):
        session = LiveSession(agent=None, on_output=lambda _: None)
        original = event("今天聊什么")
        self.assertIs(session._with_song_request(original), original)


if __name__ == "__main__":
    unittest.main()
