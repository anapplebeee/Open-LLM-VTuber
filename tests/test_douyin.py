from __future__ import annotations

import unittest

from src.open_llm_vtuber.live.douyin import map_douyin_payload


class DouyinAdapterTest(unittest.TestCase):
    def test_webcast_chat_maps_to_room_event(self):
        event = map_douyin_payload(
            {
                "method": "WebcastChatMessage",
                "data": {
                    "message_id": "m1",
                    "room_id": "room-1",
                    "create_time": 1_700_000_000,
                    "content": "主播你好",
                    "user": {"id": "u1", "nickname": "小鱼"},
                },
            }
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.platform, "douyin")
        self.assertEqual(event.type, "chat")
        self.assertEqual(event.user.name, "小鱼")
        self.assertEqual(event.content, "主播你好")
        self.assertEqual(event.received_at_ms, 1_700_000_000_000)

    def test_webcast_gift_maps_count(self):
        event = map_douyin_payload(
            {
                "type": "gift",
                "user": {"user_id": "u2", "nickname": "小蓝"},
                "gift": {"name": "小心心", "count": 3},
            }
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.type, "gift")
        self.assertEqual(event.gift.count, 3)  # type: ignore[union-attr]

    def test_like_and_enter_are_not_speech_events(self):
        self.assertIsNone(map_douyin_payload({"method": "WebcastLikeMessage"}))
        self.assertIsNone(map_douyin_payload({"method": "WebcastRoomUserSeqMessage"}))


if __name__ == "__main__":
    unittest.main()
