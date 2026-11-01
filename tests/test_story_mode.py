from __future__ import annotations

import unittest

from src.open_llm_vtuber.agent.agents.live_streamer_agent import LiveStreamerAgent
from src.open_llm_vtuber.agent.input_types import BatchInput, TextData, TextSource
from src.open_llm_vtuber.live.personality import LiveInteractionPolicy
from src.open_llm_vtuber.live.story import (
    LiveStoryTeller,
    detect_story_command,
    split_story_text,
)
from src.open_llm_vtuber.live.events import LiveBatch, RoomEvent


class FakeLLM:
    async def chat_completion(self, _messages, _system):
        yield "第一句。第二句。第三句。"


def story_batch(text: str = "讲个故事") -> LiveBatch:
    return LiveBatch(
        id="story-batch",
        kind="chat",
        priority=40,
        events=(
            RoomEvent(
                id="story-event",
                type="chat",
                platform="test",
                received_at_ms=1,
                user={"id": "u1", "name": "观众"},
                content=text,
            ),
        ),
    )


class StoryModeTest(unittest.IsolatedAsyncioTestCase):
    def test_story_commands(self):
        self.assertEqual(detect_story_command("讲个故事").action, "start")
        self.assertEqual(detect_story_command("开始讲个故事").action, "start")
        self.assertEqual(detect_story_command("继续").action, "continue")
        self.assertEqual(detect_story_command("别讲了").action, "stop")
        self.assertIsNone(detect_story_command("故事真好听"))

    def test_story_is_split_into_bounded_chunks(self):
        chunks = split_story_text("第一句。" * 100, max_chars=50)
        self.assertTrue(chunks)
        self.assertTrue(all(len(chunk) <= 50 for chunk in chunks))

    async def test_story_teller_returns_long_text(self):
        teller = LiveStoryTeller(FakeLLM())
        text = await teller.tell("深夜电台")
        self.assertIn("第一句", text)
        self.assertGreater(len(text), 5)

    async def test_agent_story_path_and_interrupt(self):
        class FakeTeller:
            async def tell(self, _topic, **_kwargs):
                return "第一段故事。" * 30

        agent = object.__new__(LiveStreamerAgent)
        agent._interaction_policy = LiveInteractionPolicy()
        agent._story_teller = FakeTeller()
        agent._story_active = False
        agent._story_interrupted = False
        agent._story_previous = ""
        agent._system = "小鲸"
        agent._add_message = lambda *_args, **_kwargs: None

        seen = []

        async def fake_stream(text):
            seen.append(text)
            yield object()

        agent._stream_live_text = fake_stream
        request = BatchInput(
            texts=[],
            images=None,
            files=None,
            metadata={"live_batch": story_batch()},
        )
        outputs = [item async for item in agent._live_chat(request)]
        self.assertGreater(len(outputs), 1)
        self.assertGreater(len(seen), 1)
        self.assertFalse(agent.is_story_active())


if __name__ == "__main__":
    unittest.main()
