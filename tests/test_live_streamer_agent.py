from __future__ import annotations

import unittest
from unittest.mock import patch

from src.open_llm_vtuber.agent.agents.basic_memory_agent import BasicMemoryAgent
from src.open_llm_vtuber.agent.agents.live_streamer_agent import LiveStreamerAgent
from src.open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource
from src.open_llm_vtuber.agent.output_types import Actions, DisplayText, SentenceOutput


def _output(text: str) -> SentenceOutput:
    return SentenceOutput(
        display_text=DisplayText(text=text),
        tts_text=text,
        actions=Actions(),
    )


class LiveStreamerWrapperTest(unittest.IsolatedAsyncioTestCase):
    async def test_live_agent_binds_around_basic_chat_and_caps_two_sentences(self):
        async def fake_base(_input_data):
            yield _output("第一句。")
            yield _output("第二句！")
            yield _output("第三句不该出现。")
            yield _output("🐳")

        def fake_init(self, **_kwargs):
            self._system = ""
            self._memory = []
            self._llm = object()
            self._tts_preprocessor_config = None
            self._live2d_model = None
            self._faster_first_response = True
            self._segment_method = "pysbd"
            self.chat = fake_base

        with patch.object(BasicMemoryAgent, "__init__", fake_init), patch.object(
            BasicMemoryAgent, "set_system", lambda _self, _system: None
        ):
            agent = LiveStreamerAgent()

        self.assertIs(agent.chat.__func__, LiveStreamerAgent._live_chat)

        request = BatchInput(
            texts=[TextData(source=TextSource.INPUT, content="测试")],
            images=None,
            files=None,
        )
        outputs = [item async for item in agent.chat(request)]
        self.assertEqual([item.display_text.text for item in outputs], ["第一句。", "第二句！"])


if __name__ == "__main__":
    unittest.main()
