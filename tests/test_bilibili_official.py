from __future__ import annotations

import unittest
from unittest.mock import patch

from src.open_llm_vtuber.live.bilibili import map_bilibili_official_message
from src.open_llm_vtuber.live.collectors.bilibili_official import (
    BilibiliOfficialConfig,
)


class BilibiliOfficialMappingTest(unittest.TestCase):
    def test_danmaku_becomes_chat_event(self):
        event = map_bilibili_official_message(
            {
                "cmd": "LIVE_OPEN_PLATFORM_DM",
                "data": {
                    "open_id": "open-1",
                    "uname": "小蓝",
                    "msg": "主播好",
                    "msg_id": "m1",
                    "room_id": 123,
                    "timestamp": 1000,
                },
            }
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.type, "chat")
        self.assertEqual(event.user.id, "open-1")
        self.assertEqual(event.received_at_ms, 1_000_000)

    def test_gift_uses_combo_count_and_sc_preserves_message(self):
        gift = map_bilibili_official_message(
            {
                "cmd": "LIVE_OPEN_PLATFORM_SEND_GIFT",
                "data": {
                    "open_id": "open-1",
                    "uname": "小蓝",
                    "gift_name": "星星",
                    "gift_num": 1,
                    "combo_info": {"combo_count": 5},
                    "msg_id": "g1",
                    "timestamp": 1000,
                },
            }
        )
        self.assertIsNotNone(gift)
        self.assertEqual(gift.gift.count, 5)
        sc = map_bilibili_official_message(
            {
                "cmd": "LIVE_OPEN_PLATFORM_SUPER_CHAT",
                "data": {
                    "open_id": "open-1",
                    "uname": "小蓝",
                    "message": "点歌",
                    "message_id": 9,
                    "timestamp": 1000,
                },
            }
        )
        self.assertIsNotNone(sc)
        self.assertEqual(sc.type, "gift")
        self.assertEqual(sc.content, "点歌")

    def test_state_events_and_missing_identity_are_skipped(self):
        self.assertIsNone(
            map_bilibili_official_message(
                {"cmd": "LIVE_OPEN_PLATFORM_WATCHED_CHANGE", "data": {}}
            )
        )
        self.assertIsNone(
            map_bilibili_official_message(
                {
                    "cmd": "LIVE_OPEN_PLATFORM_DM",
                    "data": {"msg": "no identity"},
                }
            )
        )

    def test_config_requires_credentials(self):
        with self.assertRaises(ValueError):
            BilibiliOfficialConfig("", 0, "", "").validate()

    def test_config_reads_environment_without_exposing_secret(self):
        env = {
            "BILI_ID_CODE": "room-owner-code",
            "BILI_APP_ID": "12345",
            "BILI_ACCESS_KEY": "key",
            "BILI_ACCESS_KEY_SECRET": "super-secret-value",
        }
        with patch.dict("os.environ", env, clear=False):
            config = BilibiliOfficialConfig.from_env()
        self.assertEqual(config.app_id, 12345)
        self.assertEqual(config.id_code, "room-owner-code")
        self.assertNotIn("super-secret-value", repr(config))


if __name__ == "__main__":
    unittest.main()
