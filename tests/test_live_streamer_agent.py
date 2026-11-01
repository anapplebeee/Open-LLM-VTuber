from __future__ import annotations

import unittest
from unittest.mock import patch

from src.open_llm_vtuber.agent.agents.basic_memory_agent import BasicMemoryAgent
from src.open_llm_vtuber.agent.agents.live_streamer_agent import LiveStreamerAgent
from src.open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource
from src.open_llm_vtuber.agent.output_types import Actions, DisplayText, SentenceOutput
from src.open_llm_vtuber.live.events import LiveBatch, RoomEvent
from src.open_llm_vtuber.live.personality import (
    GENERIC_FALLBACK,
    IDENTITY_FALLBACK,
    LiveInteractionPolicy,
)
from src.open_llm_vtuber.live.planner import LiveDecision


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

    async def test_kaomoji_is_converted_and_action_is_mirrored(self):
        async def fake_base(_input_data):
            yield _output("好开心 (≧▽≦)")

        class FakeLive2D:
            @staticmethod
            def extract_emotion(text):
                return ["happy"] if "[joy]" in text else []

        def fake_init(self, **_kwargs):
            self._system = ""
            self._memory = []
            self._llm = object()
            self._tts_preprocessor_config = None
            self._live2d_model = FakeLive2D()
            self._faster_first_response = True
            self._segment_method = "pysbd"
            self.chat = fake_base

        with patch.object(BasicMemoryAgent, "__init__", fake_init), patch.object(
            BasicMemoryAgent, "set_system", lambda _self, _system: None
        ):
            agent = LiveStreamerAgent()

        request = BatchInput(
            texts=[TextData(source=TextSource.INPUT, content="测试颜文字")],
            images=None,
            files=None,
        )
        outputs = [item async for item in agent.chat(request)]
        self.assertEqual(outputs[0].display_text.text, "[joy]好开心。")
        self.assertEqual(outputs[0].actions.expressions, ["happy"])

    async def test_identity_claim_is_replaced_once_and_followup_is_suppressed(self):
        async def fake_base(_input_data):
            yield _output("我是 DeepSeek，")
            yield _output("由深度求索公司创造的 AI 助手。")
            yield _output("我可以处理文件。")

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

        request = BatchInput(
            texts=[TextData(source=TextSource.INPUT, content="你是谁")],
            images=None,
            files=None,
        )
        outputs = [item async for item in agent.chat(request)]
        self.assertEqual([item.display_text.text for item in outputs], [IDENTITY_FALLBACK])

    async def test_generic_capability_list_gets_a_short_host_fallback(self):
        async def fake_base(_input_data):
            yield _output("我能做这些事：对话问答、写作与改写、文档处理。")

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

        request = BatchInput(
            texts=[TextData(source=TextSource.INPUT, content="你能做什么")],
            images=None,
            files=None,
        )
        outputs = [item async for item in agent.chat(request)]
        self.assertEqual(
            [item.display_text.text for item in outputs],
            [GENERIC_FALLBACK],
        )

    async def test_live_batch_reaches_planner_and_replyer(self):
        calls = []

        class FakePlanner:
            def plan(self, _batch, **_kwargs):
                return LiveDecision(True, "test-reply", "说一句")

        class FakeReplyer:
            async def reply(self, _decision, _batch, **_kwargs):
                calls.append("replyer")
                return "小鲸在听。"

        agent = object.__new__(LiveStreamerAgent)
        agent._live_planner = FakePlanner()
        agent._live_replyer = FakeReplyer()
        agent._rundown_state = None
        agent._memory = []
        agent._session_recap = ""
        agent._system = "小鲸"
        agent._interaction_policy = LiveInteractionPolicy()
        agent._add_message = lambda *_args, **_kwargs: None

        async def fake_stream(text):
            calls.append("stream")
            yield _output(text)

        agent._stream_live_text = fake_stream
        batch = LiveBatch(
            id="live-batch",
            kind="chat",
            priority=40,
            events=(
                RoomEvent(
                    id="event-1",
                    type="chat",
                    platform="test",
                    received_at_ms=1,
                    user={"id": "u1", "name": "观众"},
                    content="今天聊什么",
                ),
            ),
        )
        request = BatchInput(
            texts=[],
            images=None,
            files=None,
            metadata={"live_batch": batch},
        )
        outputs = [item async for item in agent._live_chat(request)]
        self.assertEqual([item.display_text.text for item in outputs], ["小鲸在听。"])
        self.assertEqual(calls, ["replyer", "stream"])


if __name__ == "__main__":
    unittest.main()
