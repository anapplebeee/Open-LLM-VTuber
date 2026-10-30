from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from src.open_llm_vtuber.tts.gpt_sovits_tts import TTSEngine


class GPTSoVITSTest(unittest.TestCase):
    def test_api_url_is_normalized_to_tts_endpoint(self):
        engine = TTSEngine(api_url="http://127.0.0.1:9880")
        self.assertEqual(engine.api_url, "http://127.0.0.1:9880/tts")

    def test_api_v2_request_carries_reference_fields_and_writes_audio(self):
        engine = TTSEngine(
            api_url="http://127.0.0.1:9880/tts",
            ref_audio_path="referenceAudio/xiaoyi_ref.wav",
            prompt_text="参考文本",
            streaming_mode=0,
        )
        fake_response = type(
            "Response",
            (),
            {"status_code": 200, "content": b"RIFF-test-audio", "text": ""},
        )()
        with patch(
            "src.open_llm_vtuber.tts.gpt_sovits_tts.requests.get",
            return_value=fake_response,
        ) as request:
            output = engine.generate_audio("你好", "gpt-sovits-test")
        try:
            self.assertIsNotNone(output)
            self.assertEqual(Path(output).read_bytes(), b"RIFF-test-audio")
            params = request.call_args.kwargs["params"]
            self.assertEqual(params["text"], "你好")
            self.assertEqual(params["ref_audio_path"], "referenceAudio/xiaoyi_ref.wav")
            self.assertEqual(params["prompt_text"], "参考文本")
            self.assertEqual(params["streaming_mode"], 0)
            self.assertEqual(request.call_args.kwargs["timeout"], (10, 120))
        finally:
            if output:
                Path(output).unlink(missing_ok=True)

    def test_missing_reference_is_rejected_before_request(self):
        engine = TTSEngine(api_url="http://127.0.0.1:9880/tts")
        with patch(
            "src.open_llm_vtuber.tts.gpt_sovits_tts.requests.get"
        ) as request:
            self.assertIsNone(engine.generate_audio("你好", "missing-ref"))
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
