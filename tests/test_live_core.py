from __future__ import annotations

import asyncio
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from src.open_llm_vtuber.config_manager.agent import AgentConfig
from src.open_llm_vtuber.live.bilibili import map_bilibili_payload
from src.open_llm_vtuber.live.director import LiveDirector
from src.open_llm_vtuber.live.events import LiveBatch, RoomEvent
from src.open_llm_vtuber.live.planner import RuleBasedLivePlanner
from src.open_llm_vtuber.live.proactive import ProactiveScheduler
from src.open_llm_vtuber.live.rundown import RundownState
from src.open_llm_vtuber.live.runtime import LiveSession
from src.open_llm_vtuber.live.store import LiveStore


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

    async def test_full_queue_evicts_oldest_low_priority_item(self):
        director = LiveDirector(window_ms=0, max_queue=2, now_ms=lambda: 1000)
        batches: list[LiveBatch] = []
        director._handler = lambda batch: _append(batches, batch)
        self.assertTrue(director.ingest(event("chat-old")))
        self.assertTrue(director.ingest(event("chat-new")))
        self.assertTrue(director.ingest(event("gift-new", "gift")))
        self.assertEqual(director.status()["queued"], 2)
        await director.drain_once()
        await director.drain_once()
        self.assertEqual([batch.kind for batch in batches], ["gift", "chat"])
        self.assertEqual([item.id for item in batches[1].events], ["chat-new"])

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

    async def test_gift_is_not_mixed_and_precedes_a_queued_chat_backlog(self):
        director = LiveDirector(window_ms=0, now_ms=lambda: 1000)
        batches: list[LiveBatch] = []
        director._handler = lambda batch: _append(batches, batch)
        director.ingest(event("chat-1"))
        director.ingest(event("chat-2"))
        director.ingest(event("gift-1", "gift"))
        await director.drain_once()
        await director.drain_once()
        self.assertEqual([batch.kind for batch in batches], ["gift", "chat"])
        self.assertEqual([item.id for item in batches[1].events], ["chat-1", "chat-2"])

    def test_live_session_loads_rundown_into_agent(self):
        class FakeAgent:
            _rundown_state = None

        session = LiveSession(
            agent=FakeAgent(),
            on_output=_async_none,
            session_id="rundown-test",
        )
        snapshot = session.load_rundown(
            [{"id": "open", "title": "开场", "goal": "问候", "expected_ms": 1000}],
            auto_begin=True,
        )
        self.assertEqual(snapshot["status"], "running")
        self.assertIsNotNone(session.agent._rundown_state)
        self.assertIn("开场", session.agent._rundown_state.context_text())
        self.assertEqual(session.control_rundown("next")["index"], 0)
        self.assertEqual(session.control_rundown("finish")["status"], "done")

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

    def test_proactive_scheduler_resets_and_caps_consecutive_lines(self):
        scheduler = ProactiveScheduler(
            enabled=True, interval_ms=1000, max_consecutive=1
        )
        self.assertFalse(scheduler.due(1000))
        self.assertTrue(scheduler.due(2000))
        scheduler.mark_spoken(2000)
        self.assertFalse(scheduler.due(3000))
        scheduler.note_user_activity(4000)
        self.assertTrue(scheduler.due(5000))

    async def test_proactive_tick_emits_one_batch_when_rundown_is_running(self):
        outputs: list[object] = []

        class FakeAgent:
            _rundown_state = None

            async def chat(self, _input_data):
                yield "proactive-output"

        session = LiveSession(
            agent=FakeAgent(),
            on_output=lambda output: _append(outputs, output),
            session_id="proactive-test",
            rundown=RundownState(
                [{"id": "open", "title": "开场", "goal": "问候", "expected_ms": 1000}]
            ),
            now_ms=lambda: 2000,
        )
        await session.start()
        session.set_proactive(enabled=True, interval_ms=1000)
        first = await session.proactive_tick(1000)
        self.assertIsNone(first)
        second = await session.proactive_tick(2000)
        self.assertIsNotNone(second)
        self.assertEqual(second.kind, "proactive")
        self.assertEqual(outputs, ["proactive-output"])
        await session.stop()

    async def test_live_session_builds_context_and_updates_emotion(self):
        class FakeAgent:
            _rundown_state = None

            def __init__(self):
                self.context = None

            def set_live_context(self, **kwargs):
                self.context = kwargs

            async def chat(self, _input_data):
                yield "好呀。"

        agent = FakeAgent()
        session = LiveSession(
            agent=agent,
            on_output=lambda output: _append([], output),
            session_id="context-test",
            now_ms=lambda: 1000,
        )
        await session.start()
        live_batch = LiveBatch(
            id="context-batch",
            kind="chat",
            priority=40,
            events=(event("context-event", text="谢谢你，真棒"),),
        )
        await session._handle_batch(live_batch)
        self.assertIsNotNone(agent.context)
        packet = agent.context["context_packet"]
        self.assertIn("小鲸", packet.prompt_block())
        self.assertEqual(packet.emotion.label, "joy")
        self.assertEqual(session.status()["emotion"]["label"], "joy")
        await session.stop()

    async def test_live_session_forwards_one_batch_to_agent_and_output(self):
        outputs: list[object] = []
        ended: list[bool] = []
        seen: dict[str, object] = {}

        class FakeAgent:
            async def chat(self, input_data):
                seen["metadata"] = input_data.metadata
                yield "SentenceOutput"

        session = LiveSession(
            agent=FakeAgent(),
            on_output=lambda output: _append(outputs, output),
            on_batch_end=lambda: _append(ended, True),
            director=LiveDirector(window_ms=0, now_ms=lambda: 1000),
        )
        await session.start()
        self.assertTrue(session.ingest(event("session-chat")))
        await asyncio.wait_for(_wait_until(lambda: bool(ended)), timeout=1)
        self.assertEqual(outputs, ["SentenceOutput"])
        self.assertEqual(ended, [True])
        self.assertIn("live_batch", seen["metadata"])
        await session.stop()

    async def test_bilibili_collector_callback_is_awaited(self):
        class FakeAgent:
            _rundown_state = None

            async def chat(self, _input_data):
                if False:
                    yield None

        class FakeCollector:
            instances = []

            def __init__(self, config):
                self.config = config
                self.stopped = False
                FakeCollector.instances.append(self)

            async def start(self, on_event):
                # The collector awaits its callback; a synchronous `ingest`
                # passed directly would raise "bool is not awaitable".
                await on_event(event("collector-event"))

            async def stop(self):
                self.stopped = True

        session = LiveSession(
            agent=FakeAgent(),
            on_output=_async_none,
            session_id="collector-test",
            now_ms=lambda: 1000,
        )
        await session.start()
        with patch(
            "src.open_llm_vtuber.live.collectors.bilibili_official.BilibiliOfficialCollector",
            FakeCollector,
        ):
            await session.start_bilibili(object())
        self.assertEqual(session.director.status()["accepted"], 1)
        await session.stop()
        self.assertTrue(FakeCollector.instances[0].stopped)

    async def test_live_session_persists_user_side_of_dialogue(self):
        class FakeAgent:
            _rundown_state = None

            async def chat(self, _input_data):
                if False:
                    yield None

        db_path = Path("data") / f"test-live-chat-{uuid.uuid4().hex}.db"
        store = LiveStore(db_path)
        session = LiveSession(
            agent=FakeAgent(),
            on_output=_async_none,
            session_id="chat-persist",
            store=store,
            now_ms=lambda: 1000,
        )
        await session.start()
        batch = LiveBatch(
            id="chat-batch",
            kind="chat",
            priority=40,
            events=(event("chat-event", text="你好"),),
        )
        await session._handle_batch(batch)
        chats = store.recent_chat("chat-persist")
        self.assertTrue(any(row["role"] == "user" for row in chats))
        self.assertTrue(any("你好" in row["content"] for row in chats))
        await session.stop()
        self.assertEqual(store.get_session("chat-persist")["status"], "ended")
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{db_path}{suffix}")
            if path.exists():
                path.unlink()

    async def test_auto_begun_rundown_can_still_start_session(self):
        class FakeAgent:
            _rundown_state = None

            async def chat(self, _input_data):
                if False:
                    yield None

        session = LiveSession(
            agent=FakeAgent(),
            on_output=_async_none,
            session_id="auto-begin",
            now_ms=lambda: 1000,
        )
        session.load_rundown(
            [{"id": "open", "title": "开场", "goal": "问候", "expected_ms": 1000}],
            auto_begin=True,
        )
        await session.start()
        self.assertEqual(session.rundown.status, "running")
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


async def _async_none(*_args, **_kwargs) -> None:
    return None


async def _wait_until(predicate) -> None:
    while not predicate():
        await asyncio.sleep(0.01)
