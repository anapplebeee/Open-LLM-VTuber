from __future__ import annotations

import unittest

from src.open_llm_vtuber.agent.output_types import Actions, DisplayText
from src.open_llm_vtuber.utils.stream_audio import prepare_audio_payload


class AudioPayloadTest(unittest.TestCase):
    def test_audio_payload_exposes_top_level_expressions(self):
        payload = prepare_audio_payload(
            audio_path=None,
            display_text=DisplayText(text="你好"),
            actions=Actions(expressions=["happy"]),
        )
        self.assertEqual(payload["expressions"], ["happy"])
        self.assertEqual(payload["actions"]["expressions"], ["happy"])

    def test_audio_payload_without_actions_has_null_expressions(self):
        payload = prepare_audio_payload(
            audio_path=None,
            display_text=DisplayText(text="你好"),
            actions=None,
        )
        self.assertIsNone(payload["expressions"])
        self.assertIsNone(payload["actions"])


if __name__ == "__main__":
    unittest.main()
