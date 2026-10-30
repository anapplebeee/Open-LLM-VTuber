from __future__ import annotations

import unittest
from collections.abc import AsyncIterator
from typing import Any

from src.open_llm_vtuber.live.events import LiveBatch, RoomEvent
from src.open_llm_vtuber.live.planner import LiveReActPlanner
from src.open_llm_vtuber.live.replyer import LiveReplyer


class FakeLLM:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0
        self.system_prompts: list[str] = []

    async def chat_completion(
        self, messages: list[dict[str, Any]], system: str | None = None
    ) -> AsyncIterator[str]:
        self.system_prompts.append(system or "")
        response = self.responses[self.calls]
        self.calls += 1
        yield response


def batch(kind: str = "chat") -> LiveBatch:
    return LiveBatch(
        id="b1",
        kind=kind,
        priority=40,
        events=(
            RoomEvent(
                id="e1",
                type="chat",
                platform="simulated",
                received_at_ms=1000,
                user={"id": "u1", "name": "小蓝"},
                content="主播今天吃什么",
            ),
        ),
    )


class LivePlannerReplyerTest(unittest.IsolatedAsyncioTestCase):
    async def test_planner_returns_structured_decision(self):
        llm = FakeLLM(
            [
                (
                    '{"action":"reply","reason":"观众提问",'
                    '"instruction":"回答今天吃什么","emotion":"happy","intensity":0.8}'
                )
            ]
        )
        decision = await LiveReActPlanner(llm).plan(batch())
        self.assertTrue(decision.speak)
        self.assertEqual(decision.prompt, "回答今天吃什么")
        self.assertEqual(decision.emotion, "happy")
        self.assertAlmostEqual(decision.intensity, 0.8)

    async def test_planner_can_stay_silent(self):
        llm = FakeLLM(['{"action":"silent","reason":"没有有效内容"}'])
        decision = await LiveReActPlanner(llm).plan(batch())
        self.assertFalse(decision.speak)
        self.assertEqual(decision.reason, "没有有效内容")

    async def test_replyer_generates_only_speech(self):
        llm = FakeLLM(["今天吃鱼，感谢小蓝的弹幕。"])
        replyer = LiveReplyer(llm)
        decision = await LiveReActPlanner(
            FakeLLM(
                [
                    (
                        '{"action":"reply","reason":"提问",'
                        '"instruction":"回答吃什么","emotion":"happy","intensity":0.8}'
                    )
                ]
            )
        ).plan(batch())
        text = await replyer.reply(decision, batch())
        self.assertEqual(text, "今天吃鱼，感谢小蓝的弹幕。")
        self.assertIn("表达引擎", llm.system_prompts[0])


if __name__ == "__main__":
    unittest.main()
