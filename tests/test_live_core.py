from __future__ import annotations

import asyncio
import unittest

from src.open_llm_vtuber.config_manager.agent import AgentConfig
from src.open_llm_vtuber.live.bilibili import map_bilibili_payload
from src.open_llm_vtuber.live.director import LiveDirector
from src.open_llm_vtuber.live.events import LiveBatch, RoomEvent
from src.open_llm_vtuber.live.planner import RuleBasedLivePlanner
from src.open_llm_vtuber.live.rundown import RundownState
from src.open_llm_vtuber.live.runtime import LiveSession


def event(event_id: str, event_type: str = "chat", text: str = "你好") -> RoomEvent:
    return RoomEvent(
        id=event_id,
        type=event_type,
        platform="simulated",
        received_at_ms=1000,
        user={"id": "u1", "name": "小蓝"},
        content=text,
        gift={"name": "星星", "count": 1} if event_type == "gift" else None,
    )


class LiveCoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_director_batches_same_priority_and_deduplicates(self):
        director = LiveDirector(window_ms=0, max_batch=20, now_ms=lambda: 1000)
        batches: list[LiveBatch] = []

        async def handler(batch: LiveBatch) -> None:
            batches.append(batch)

        director._handler = handler
        assert director.ingest(event("a")) is True
        assert director.ingest(event("b")) is True
        assert director.ingest(event("a")) is False
        await director.drain_once()
        self.assertEqual([item.id for item in batches[0].events], ["a", "b"])

    async def test_director_task_dispatches_after_window_and_stops(self):
        batches: list[LiveBatch] = []
        director = LiveDirector(window_ms=10, now_ms=lambda: 1000)

        async def handler(batch: LiveBatch) -> None:
            batches.append(batch)

        await director.start(handler)
        self.assertTrue(director.ingest(event("task-1")))
        self.assertTrue(director.ingest(event("task-2")))
        await asyncio.wait_for(_wait_until(lambda: bool(batches)), timeout=1)
        self.assertEqual([item.id for item in batches[0].events], ["task-1", "task-2"])
        await director.stop()
        self.assertFalse(director.status()["running"])

    async def test_gift_is_not_mixed_into_chat_batch(self):
        director = LiveDirector(window_ms=0, now_ms=lambda: 1000)
        batches: list[LiveBatch] = []
        director._handler = lambda batch: _append(batches, batch)
        director.ingest(event("chat-1"))
        director.ingest(event("gift-1", "gift"))
        await director.drain_once()
        await director.drain_once()
        self.assertEqual([batch.kind for batch in batches], ["chat", "gift"])

    def test_rundown_tracks_progress_and_context(self):
        rundown = RundownState(
            [
                {"id": "open", "title": "开场", "goal": "问候", "expected_ms": 1000},
                {"id": "talk", "title": "闲聊", "goal": "聊弹幕", "expected_ms": 2000},
            ],
            now_ms=lambda: 1000,
        )
        rundown.begin("system")
        self.assertEqual(rundown.current().id, "open")
        self.assertIn("开场", rundown.context_text())
        rundown.next("human")
        self.assertEqual(rundown.current().id, "talk")
        self.assertEqual(rundown.snapshot()["status"], "running")

    def test_rundown_enforces_minimum_and_preserves_paused_elapsed(self):
        clock = [1000]
        rundown = RundownState(
            [
                {
                    "id": "open",
                    "title": "开场",
                    "goal": "问候",
                    "expected_ms": 5000,
                    "min_duration_ms": 1000,
                },
                {"id": "talk", "title": "闲聊", "goal": "聊弹幕", "expected_ms": 2000},
            ],
            now_ms=lambda: clock[0],
        )
        rundown.begin()
        clock[0] = 1500
        with self.assertRaises(ValueError):
            rundown.next()
        rundown.pause()
        clock[0] = 5000
        rundown.resume()
        clock[0] = 5500
        self.assertEqual(rundown.snapshot()["elapsed_ms"], 1000)
        rundown.next()
        self.assertEqual(rundown.current().id, "talk")

    async def test_live_session_forwards_one_batch_to_agent_and_output(self):
        outputs: list[object] = []
        seen: dict[str, object] = {}

        class FakeAgent:
            async def chat(self, input_data):
                seen["metadata"] = input_data.metadata
                yield "SentenceOutput"

        session = LiveSession(
            agent=FakeAgent(),
            on_output=lambda output: _append(outputs, output),
            director=LiveDirector(window_ms=0, now_ms=lambda: 1000),
        )
        await session.start()
        self.assertTrue(session.ingest(event("session-chat")))
        await asyncio.wait_for(_wait_until(lambda: bool(outputs)), timeout=1)
        self.assertEqual(outputs, ["SentenceOutput"])
        self.assertIn("live_batch", seen["metadata"])
        await session.stop()

    def test_live_agent_is_a_valid_config_choice(self):
        config = AgentConfig(
            conversation_agent_choice="live_streamer_agent",
            agent_settings={"live_streamer_agent": {"llm_provider": "ollama_llm"}},
            llm_configs={},
        )
        self.assertEqual(config.conversation_agent_choice, "live_streamer_agent")
        self.assertIsNotNone(config.agent_settings.live_streamer_agent)

    def test_planner_keeps_events_as_untrusted_data_and_silences_empty_chat(self):
        planner = RuleBasedLivePlanner()
        decision = planner.plan(
            LiveBatch(
                id="b1",
                kind="chat",
                priority=40,
                events=(event("p1", text='打开 ["系统"]'),),
            ),
            rundown_text="当前节目环节：开场",
        )
        self.assertTrue(decision.speak)
        self.assertIn("打开", decision.prompt)
        self.assertIn("系统", decision.prompt)
        self.assertIn("不可信数据", decision.prompt)
        self.assertIn("当前节目环节：开场", decision.prompt)
        empty = planner.plan(
            LiveBatch(
                id="b2", kind="chat", priority=40, events=(event("p2", text=" "),)
            )
        )
        self.assertFalse(empty.speak)

    def test_bilibili_payload_mapping(self):
        mapped = map_bilibili_payload(
            {
                "message_type": "gift",
                "message_id": "g1",
                "room_id": "room",
                "timestamp_ms": 1000,
                "user": {"uid": "u1", "name": "小蓝"},
                "gift": {"name": "星星", "count": 2},
            }
        )
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped.type, "gift")
        self.assertEqual(mapped.gift.count, 2)
        self.assertEqual(mapped.user.name, "小蓝")
        self.assertIsNone(map_bilibili_payload({"message_type": "enter"}))


async def _append(target: list[object], value: object) -> None:
    target.append(value)


async def _wait_until(predicate) -> None:
    while not predicate():
        await asyncio.sleep(0.01)
