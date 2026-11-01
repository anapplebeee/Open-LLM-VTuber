from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.open_llm_vtuber.live.events import GiftInfo, RoomEvent
from src.open_llm_vtuber.live.review import LiveReviewLogger


class ReviewLoggerTest(unittest.TestCase):
    def test_records_danmaku_gift_and_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "review.jsonl"
            logger = LiveReviewLogger(path)
            chat = RoomEvent(
                id="e1",
                type="chat",
                platform="douyin",
                received_at_ms=1_700_000_000_000,
                user={"id": "u1", "name": "小鱼"},
                content="主播你好",
            )
            gift = RoomEvent(
                id="e2",
                type="gift",
                platform="douyin",
                received_at_ms=1_700_000_001_000,
                user={"id": "u2", "name": "小蓝"},
                gift=GiftInfo(name="小心心", count=3),
            )
            record = logger.record(
                [chat, gift], "你好呀。", session_id="s1", source="live"
            )
            self.assertIsNotNone(record)
            raw = path.read_text(encoding="utf-8").strip()
            parsed = json.loads(raw)
            self.assertEqual(parsed["session_id"], "s1")
            self.assertEqual(parsed["reply"], "你好呀。")
            self.assertEqual(parsed["events"][0]["user"], "小鱼")
            self.assertEqual(parsed["events"][1]["gift"]["name"], "小心心")


if __name__ == "__main__":
    unittest.main()
