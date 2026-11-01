from __future__ import annotations

import unittest

from src.open_llm_vtuber.live.context import StreamContextBuilder
from src.open_llm_vtuber.live.character_profile import XIAOJING_PROFILE
from src.open_llm_vtuber.live.memes import HOT_MEMES, MemeLibrary, meme_system_guide
from src.open_llm_vtuber.live.state import EmotionState, StreamState


class MemeLibraryTest(unittest.TestCase):
    def test_has_curated_memes_with_meaning_and_usage(self):
        self.assertGreaterEqual(len(HOT_MEMES), 20)
        for card in HOT_MEMES:
            self.assertTrue(card.meaning)
            self.assertTrue(card.usage)
            self.assertTrue(card.tone)

    def test_matching_meme_returns_meaning_and_usage_rules(self):
        hint = MemeLibrary().hint_for_text("你说你没摸鱼？我要验牌")
        self.assertIn("我要验牌", hint)
        self.assertIn("亮出证据", hint)
        self.assertIn("不要解释梗", hint)

    def test_serious_message_does_not_force_a_meme(self):
        self.assertEqual(MemeLibrary().hint_for_text("我今天真的很难过"), "")

    def test_system_guide_has_restraint_rules(self):
        guide = meme_system_guide()
        self.assertIn("最多自然带一个梗", guide)
        self.assertIn("认真求助", guide)

    def test_context_packet_carries_meme_hint(self):
        packet = StreamContextBuilder().build(
            profile=XIAOJING_PROFILE,
            emotion=EmotionState(),
            stream=StreamState(),
            viewer=None,
            events_text="观众：我要验牌",
            meme_hint=MemeLibrary().hint_for_text("我要验牌"),
        )
        text = packet.prompt_block()
        self.assertIn("热梗参考", text)
        self.assertIn("我要验牌", text)
        self.assertTrue(packet.trace()["has_meme_hint"])


if __name__ == "__main__":
    unittest.main()
