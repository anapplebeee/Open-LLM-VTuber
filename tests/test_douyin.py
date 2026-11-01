from __future__ import annotations

import unittest

from src.open_llm_vtuber.live.douyin import map_douyin_payload, map_dyhub_event


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

    def test_dyhub_chat_and_gift_are_mapped(self):
        chat = map_dyhub_event(
            {
                "id": "evt-1",
                "roomId": "room-1",
                "type": "chat",
                "user": {"id": "u1", "nickname": "小鱼"},
                "data": {"content": "主播你好"},
            }
        )
        self.assertIsNotNone(chat)
        assert chat is not None
        self.assertEqual(chat.platform, "douyin")
        self.assertEqual(chat.content, "主播你好")
        self.assertEqual(chat.room_id, "room-1")

        gift = map_dyhub_event(
            {
                "id": "evt-2",
                "roomId": "room-1",
                "type": "gift",
                "user": {"id": "u2", "nickname": "小蓝"},
                "data": {"giftName": "小心心", "repeatCount": 3},
            }
        )
        self.assertIsNotNone(gift)
        assert gift is not None
        self.assertEqual(gift.gift.name, "小心心")  # type: ignore[union-attr]
        self.assertEqual(gift.gift.count, 3)  # type: ignore[union-attr]
        member = map_dyhub_event(
            {
                "type": "member",
                "user": {"id": "u3", "nickname": "新观众"},
                "data": {},
            }
        )
        self.assertIsNotNone(member)
        assert member is not None
        self.assertEqual(member.type, "join")


if __name__ == "__main__":
    unittest.main()
