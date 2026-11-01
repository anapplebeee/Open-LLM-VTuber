from __future__ import annotations

import unittest

from src.open_llm_vtuber.live.douyin_policy import DOUYIN_LIVE_POLICY


class DouyinPolicyTest(unittest.TestCase):
    def test_policy_covers_core_platform_boundaries(self):
        for keyword in (
            "未成年人",
            "诱导礼物",
            "色情",
            "隐私",
            "版权",
            "AI 主播",
            "站外",
        ):
            self.assertIn(keyword, DOUYIN_LIVE_POLICY)

    def test_policy_has_actionable_refusal_style(self):
        self.assertIn("一句自然短话拒绝", DOUYIN_LIVE_POLICY)
        self.assertIn("不重复违规词", DOUYIN_LIVE_POLICY)


if __name__ == "__main__":
    unittest.main()
