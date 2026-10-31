from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from src.open_llm_vtuber.live.character_profile import XIAOJING_PROFILE
from src.open_llm_vtuber.live.context import StreamContextBuilder
from src.open_llm_vtuber.live.events import GiftInfo, LiveBatch, RoomEvent, RoomUser
from src.open_llm_vtuber.live.memory import MemoryItem, MemoryManager, MemoryStore
from src.open_llm_vtuber.live.publishing import (
    PublishApprovalError,
    PublishApprovalGate,
)
from src.open_llm_vtuber.live.state import (
    EmotionEngine,
    EmotionState,
    StreamState,
    ViewerRegistry,
    ViewerState,
    normalize_emotion_tag,
)


def event(
    *,
    event_id: str = "e1",
    content: str = "你好",
    user_id: str = "u1",
    user_name: str = "观众",
    kind: str = "chat",
    now: int = 1_000,
    gift: GiftInfo | None = None,
) -> RoomEvent:
    return RoomEvent(
        id=event_id,
        type=kind,
        platform="simulated",
        room_id="room",
        received_at_ms=now,
        user=RoomUser(id=user_id, name=user_name),
        content=content,
        gift=gift,
    )


def batch(*events: RoomEvent, kind: str = "chat") -> LiveBatch:
    return LiveBatch(
        id="batch",
        kind=kind,
        priority=40,
        events=tuple(events),
    )


class EmotionStateTest(unittest.TestCase):
    def test_gift_raises_joy_and_keeps_valid_tag(self):
        state = EmotionState().apply(
            EmotionEngine().appraise(
                batch(
                    event(kind="gift", gift=GiftInfo(name="鲸鱼", count=2), now=1000),
                    kind="gift",
                )
            ),
            1000,
        )
        self.assertGreater(state.valence, 0)
        self.assertEqual(state.expression_tag(), "joy")
        self.assertIn(state.expression_tag(), {"neutral", "anger", "disgust", "fear", "joy", "smirk", "sadness", "surprise"})

    def test_happy_alias_maps_to_joy(self):
        self.assertEqual(normalize_emotion_tag("happy"), "joy")
        self.assertEqual(normalize_emotion_tag("[SAD]"), "sadness")
        self.assertEqual(normalize_emotion_tag("unknown"), "neutral")

    def test_insult_lowers_valence_and_uses_anger(self):
        state = EmotionState().apply(
            EmotionEngine().appraise(batch(event(content="笨蛋，真讨厌"))), 1000
        )
        self.assertLess(state.valence, 0)
        self.assertEqual(state.label, "anger")

    def test_decay_returns_toward_baseline(self):
        state = EmotionState(
            valence=0.8,
            arousal=0.8,
            dominance=0.8,
            intensity=0.9,
            updated_at_ms=1_000,
        )
        decayed = state.decay(181_000)
        self.assertAlmostEqual(decayed.valence, 0.4)
        self.assertLess(decayed.intensity, state.intensity)

    def test_viewer_state_is_bounded(self):
        viewer = ViewerState(user_id="u1", name="小蓝")
        for index in range(100):
            viewer.observe(event(now=index), positive=True)
        self.assertLessEqual(viewer.familiarity, 1.0)
        self.assertLessEqual(viewer.warmth, 1.0)
        self.assertEqual(viewer.interactions, 100)

    def test_viewer_profile_can_be_restored_across_sessions(self):
        manager = MemoryManager()
        manager.save_viewer(
            ViewerState(
                user_id="u1",
                name="小蓝",
                familiarity=0.42,
                warmth=0.3,
                interactions=7,
                last_seen_ms=1000,
            )
        )
        registry = ViewerRegistry()
        registry.restore(manager.load_viewers())
        restored = registry.get("u1")
        self.assertIsNotNone(restored)
        self.assertAlmostEqual(restored.familiarity, 0.42)
        self.assertEqual(restored.interactions, 7)


class MemoryTest(unittest.TestCase):
    def test_memory_store_round_trip_and_supersede(self):
        db_path = Path("tmp") / f"memory-{uuid.uuid4().hex}.db"
        store = MemoryStore(db_path)
        try:
            old = store.add(
                MemoryItem(
                    id="old",
                    text="观众喜欢蓝色",
                    kind="preference",
                    scope="viewer",
                    owner_id="u1",
                    created_at_ms=100,
                    valid_from_ms=100,
                )
            )
            replacement = store.supersede(
                old.id,
                MemoryItem(
                    id="new",
                    text="观众现在喜欢绿色",
                    kind="preference",
                    scope="viewer",
                    owner_id="u1",
                    created_at_ms=200,
                    valid_from_ms=200,
                ),
            )
            self.assertEqual(replacement.id, "new")
            hits = store.search("喜欢", now_ms=300, scope="viewer", owner_id="u1")
            self.assertEqual([hit.item.id for hit in hits], ["new"])
            self.assertEqual(store.get("old").superseded_by, "new")
        finally:
            db_path.unlink(missing_ok=True)

    def test_viewer_profile_sqlite_round_trip(self):
        db_path = Path("tmp") / f"viewer-{uuid.uuid4().hex}.db"
        store = MemoryStore(db_path)
        try:
            store.save_viewer_profile(
                {
                    "user_id": "u9",
                    "name": "小蓝",
                    "familiarity": 0.5,
                    "warmth": 0.2,
                    "interactions": 4,
                    "last_seen_ms": 900,
                }
            )
            profiles = store.load_viewer_profiles()
            self.assertEqual(len(profiles), 1)
            self.assertEqual(profiles[0]["user_id"], "u9")
            self.assertAlmostEqual(profiles[0]["familiarity"], 0.5)
        finally:
            db_path.unlink(missing_ok=True)

    def test_memory_scope_does_not_leak_between_viewers(self):
        manager = MemoryManager()
        manager.ingest_batch(
            batch(event(event_id="e1", content="我喜欢鲸鱼", user_id="u1")),
            session_id="s1",
            now_ms=1000,
        )
        manager.ingest_batch(
            batch(event(event_id="e2", content="我喜欢企鹅", user_id="u2")),
            session_id="s1",
            now_ms=1001,
        )
        hits = manager.for_viewer("u1", now_ms=1002)
        self.assertTrue(hits)
        self.assertTrue(all(hit.item.owner_id == "u1" for hit in hits))
        self.assertNotIn("企鹅", " ".join(hit.item.text for hit in hits))

    def test_invalid_future_memory_is_not_retrieved(self):
        manager = MemoryManager()
        manager.store.add(
            MemoryItem(
                id="future",
                text="未来的事实",
                scope="room",
                created_at_ms=5000,
                valid_from_ms=5000,
            )
        )
        self.assertEqual(manager.for_room("未来", now_ms=1000), [])
        self.assertTrue(manager.for_room("未来", now_ms=6000))


class PublishingApprovalTest(unittest.TestCase):
    def test_draft_cannot_be_published_without_human_approval(self):
        gate = PublishApprovalGate()
        draft = gate.create(
            platform="bilibili",
            kind="comment",
            content="谢谢你的弹幕。",
            source_event_ids=("e1",),
        )
        self.assertFalse(gate.can_publish(draft))
        with self.assertRaises(PublishApprovalError):
            gate.approve(draft, by="system")
        approved = gate.approve(draft, by="operator")
        self.assertTrue(gate.can_publish(approved))
        self.assertEqual(approved.approved_by, "operator")

    def test_rejected_draft_is_not_publishable(self):
        gate = PublishApprovalGate()
        draft = gate.create(platform="bilibili", kind="post", content="测试动态")
        rejected = gate.reject(draft, by="operator", reason="内容需要复核")
        self.assertEqual(rejected.status, "rejected")
        self.assertFalse(gate.can_publish(rejected))


class ContextTest(unittest.TestCase):
    def test_context_contains_identity_and_trace(self):
        packet = StreamContextBuilder().build(
            profile=XIAOJING_PROFILE,
            emotion=EmotionState(label="joy", intensity=0.5),
            stream=StreamState(segment_title="开场", goal="问候"),
            viewer=ViewerState(user_id="u1", name="小蓝", interactions=2),
            events_text="观众：你好",
            memories=[],
            style_scene="greeting",
            max_chars=2000,
        )
        text = packet.prompt_block()
        self.assertIn("小鲸", text)
        self.assertIn("当前情绪状态", text)
        self.assertIn("本轮事件", text)
        trace = packet.trace()
        self.assertEqual(trace["profile_version"], "xiaojing-v1")
        self.assertEqual(trace["emotion"], "joy")

    def test_context_budget_drops_optional_sections(self):
        packet = StreamContextBuilder().build(
            profile=XIAOJING_PROFILE,
            emotion=EmotionState(),
            stream=StreamState(),
            viewer=None,
            events_text="event " * 200,
            notebook="note " * 200,
            recent_recap="recap " * 200,
            max_chars=500,
        )
        text = packet.prompt_block()
        self.assertLessEqual(len(text), 600)
        self.assertIn("上下文裁剪", text)


if __name__ == "__main__":
    unittest.main()
