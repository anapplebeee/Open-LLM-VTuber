"""Bounded, deterministic live state.

Emotion is a small state machine, not a sentence the model is allowed to
invent.  Appraisal updates are bounded; the stable character profile is never
mutated by a viewer event.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace

from .events import LiveBatch, RoomEvent

__all__ = [
    "EMOTION_TAGS",
    "EmotionAppraisal",
    "EmotionEngine",
    "EmotionState",
    "StreamState",
    "ViewerRegistry",
    "ViewerState",
    "normalize_emotion_tag",
]

EMOTION_TAGS = frozenset(
    {"neutral", "anger", "disgust", "fear", "joy", "smirk", "sadness", "surprise"}
)
_ALIASES = {
    "happy": "joy",
    "happiness": "joy",
    "angry": "anger",
    "sad": "sadness",
    "scared": "fear",
    "shocked": "surprise",
    "smug": "smirk",
}
_PRAISE = re.compile(r"谢谢|喜欢|可爱|厉害|好棒|真棒|加油|爱你|优秀|聪明|好听")
_INSULT = re.compile(r"笨蛋|蠢|垃圾|讨厌|滚|丑|无聊|闭嘴|废物")
_SAD = re.compile(r"难过|伤心|焦虑|压力|害怕|生病|去世|失恋|崩溃")
_QUESTION = re.compile(r"[?？]|吗[。！!]?$|怎么|为什么|多少")


def normalize_emotion_tag(value: str | None) -> str:
    """Map planner aliases and bracketed labels to an actual Live2D key."""
    raw = (value or "neutral").strip().lower().strip("[]")
    raw = _ALIASES.get(raw, raw)
    return raw if raw in EMOTION_TAGS else "neutral"


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class EmotionAppraisal:
    delta_valence: float = 0.0
    delta_arousal: float = 0.0
    delta_dominance: float = 0.0
    delta_energy: float = 0.0
    intensity: float = 0.2
    label: str = "neutral"
    cause_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EmotionState:
    valence: float = 0.0
    arousal: float = 0.25
    dominance: float = 0.5
    energy: float = 0.7
    label: str = "neutral"
    intensity: float = 0.2
    updated_at_ms: int = 0
    cause_event_ids: tuple[str, ...] = ()

    def decay(self, now_ms: int, *, half_life_ms: int = 180_000) -> EmotionState:
        if self.updated_at_ms <= 0 or now_ms <= self.updated_at_ms:
            return self
        if half_life_ms < 1:
            raise ValueError("half_life_ms must be positive")
        factor = 0.5 ** ((now_ms - self.updated_at_ms) / half_life_ms)
        label = self.label if self.intensity * factor >= 0.2 else "neutral"
        return replace(
            self,
            valence=_clamp(self.valence * factor),
            arousal=_clamp(0.25 + (self.arousal - 0.25) * factor, 0.0, 1.0),
            dominance=_clamp(0.5 + (self.dominance - 0.5) * factor, 0.0, 1.0),
            energy=_clamp(0.7 + (self.energy - 0.7) * factor, 0.0, 1.0),
            label=label,
            intensity=max(0.05, self.intensity * factor),
            updated_at_ms=now_ms,
        )

    def apply(self, appraisal: EmotionAppraisal, now_ms: int) -> EmotionState:
        label = normalize_emotion_tag(appraisal.label)
        # A strong event can replace the label; a weak event only nudges state.
        if appraisal.intensity >= 0.4 or label != "neutral":
            chosen_label = label
        else:
            chosen_label = self.label
        return replace(
            self,
            valence=_clamp(self.valence + appraisal.delta_valence),
            arousal=_clamp(self.arousal + appraisal.delta_arousal, 0.0, 1.0),
            dominance=_clamp(self.dominance + appraisal.delta_dominance, 0.0, 1.0),
            energy=_clamp(self.energy + appraisal.delta_energy, 0.0, 1.0),
            label=chosen_label,
            intensity=max(0.05, min(1.0, max(appraisal.intensity, self.intensity * 0.85))),
            updated_at_ms=now_ms,
            cause_event_ids=appraisal.cause_event_ids,
        )

    def expression_tag(self) -> str:
        return normalize_emotion_tag(self.label)

    def prompt_block(self) -> str:
        cause = "、".join(self.cause_event_ids[:3]) or "无"
        return (
            f"当前情绪状态：{self.label}（愉悦 {self.valence:+.2f}，"
            f"唤醒 {self.arousal:.2f}，强度 {self.intensity:.2f}，精力 {self.energy:.2f}）。"
            f"最近原因事件：{cause}。"
        )


class EmotionEngine:
    """Rule-based event appraisal with personality-weighted, bounded deltas."""

    def appraise(self, batch: LiveBatch) -> EmotionAppraisal:
        if not batch.events:
            return EmotionAppraisal(intensity=0.12, label="neutral")
        delta = [0.0, 0.0, 0.0, 0.0]
        dominant_label = "neutral"
        dominant_weight = 0.0
        causes: list[str] = []
        for event in batch.events:
            appraisal = self._appraise_event(event)
            if not appraisal.cause_event_ids:
                continue
            delta[0] += appraisal.delta_valence
            delta[1] += appraisal.delta_arousal
            delta[2] += appraisal.delta_dominance
            delta[3] += appraisal.delta_energy
            causes.extend(appraisal.cause_event_ids)
            if appraisal.intensity > dominant_weight:
                dominant_weight = appraisal.intensity
                dominant_label = appraisal.label
        return EmotionAppraisal(
            delta_valence=_clamp(delta[0], -0.35, 0.35),
            delta_arousal=_clamp(delta[1], -0.3, 0.3),
            delta_dominance=_clamp(delta[2], -0.25, 0.25),
            delta_energy=_clamp(delta[3], -0.2, 0.2),
            intensity=min(0.95, max(0.15, dominant_weight)),
            label=dominant_label,
            cause_event_ids=tuple(causes[-5:]),
        )

    def _appraise_event(self, event: RoomEvent) -> EmotionAppraisal:
        if event.type == "gift" and event.gift:
            return EmotionAppraisal(
                delta_valence=0.22,
                delta_arousal=0.16,
                delta_dominance=0.04,
                delta_energy=-0.02,
                intensity=0.7,
                label="joy",
                cause_event_ids=(event.id,),
            )
        text = event.content or ""
        if event.type == "operator":
            return EmotionAppraisal(
                delta_arousal=0.04, intensity=0.25, label="neutral", cause_event_ids=(event.id,)
            )
        if _INSULT.search(text):
            return EmotionAppraisal(
                delta_valence=-0.25,
                delta_arousal=0.18,
                delta_dominance=-0.08,
                intensity=0.72,
                label="anger",
                cause_event_ids=(event.id,),
            )
        if _SAD.search(text):
            return EmotionAppraisal(
                delta_valence=-0.12,
                delta_arousal=-0.08,
                delta_dominance=-0.05,
                intensity=0.55,
                label="sadness",
                cause_event_ids=(event.id,),
            )
        if _PRAISE.search(text):
            return EmotionAppraisal(
                delta_valence=0.16,
                delta_arousal=0.06,
                delta_dominance=0.03,
                intensity=0.55,
                label="joy",
                cause_event_ids=(event.id,),
            )
        if _QUESTION.search(text):
            return EmotionAppraisal(
                delta_valence=0.01,
                delta_arousal=0.04,
                intensity=0.25,
                label="neutral",
                cause_event_ids=(event.id,),
            )
        return EmotionAppraisal(intensity=0.12, label="neutral", cause_event_ids=(event.id,))


@dataclass
class ViewerState:
    user_id: str
    name: str
    familiarity: float = 0.0
    warmth: float = 0.0
    interactions: int = 0
    last_seen_ms: int = 0

    def observe(self, event: RoomEvent, *, positive: bool = False, negative: bool = False) -> None:
        self.interactions += 1
        self.familiarity = min(1.0, self.familiarity + 0.02)
        if positive:
            self.warmth = min(1.0, self.warmth + 0.03)
        elif negative:
            self.warmth = max(-1.0, self.warmth - 0.05)
        self.last_seen_ms = event.received_at_ms

    def prompt_block(self) -> str:
        if self.interactions <= 0:
            return ""
        return (
            f"当前观众：{self.name}；互动 {self.interactions} 次，"
            f"熟悉度 {self.familiarity:.2f}，当前好感 {self.warmth:+.2f}。"
            "只用于调整称呼和亲近程度，不假定观众隐私或现实关系。"
        )


class ViewerRegistry:
    def __init__(self) -> None:
        self._viewers: dict[str, ViewerState] = {}

    def observe(self, batch: LiveBatch, appraisal: EmotionAppraisal) -> ViewerState | None:
        if not batch.events:
            return None
        last = batch.events[-1]
        key = last.user.id or last.user.name
        viewer = self._viewers.get(key)
        if viewer is None:
            viewer = ViewerState(user_id=last.user.id, name=last.user.name)
            self._viewers[key] = viewer
        sentiment = appraisal.delta_valence
        for event in batch.events:
            viewer.observe(event, positive=sentiment > 0.05, negative=sentiment < -0.05)
        return viewer

    def get(self, user_id: str) -> ViewerState | None:
        return self._viewers.get(user_id)

    def restore(self, profiles: list[dict[str, object]]) -> None:
        for profile in profiles:
            user_id = str(profile.get("user_id") or "")
            if not user_id:
                continue
            self._viewers[user_id] = ViewerState(
                user_id=user_id,
                name=str(profile.get("name") or ""),
                familiarity=float(profile.get("familiarity", 0.0)),
                warmth=float(profile.get("warmth", 0.0)),
                interactions=int(profile.get("interactions", 0)),
                last_seen_ms=int(profile.get("last_seen_ms", 0)),
            )

    def all(self) -> list[ViewerState]:
        return list(self._viewers.values())


@dataclass
class StreamState:
    topic: str = ""
    segment_title: str = ""
    goal: str = ""
    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    last_activity_ms: int = 0

    def prompt_block(self) -> str:
        parts = []
        if self.segment_title:
            parts.append(f"当前环节：{self.segment_title}")
        if self.goal:
            parts.append(f"本段目标：{self.goal}")
        if self.topic:
            parts.append(f"当前话题：{self.topic}")
        return "；".join(parts)

    def update_from_rundown(self, rundown: object | None) -> None:
        if rundown is None or not hasattr(rundown, "current"):
            return
        current = rundown.current()
        if current is None:
            return
        self.segment_title = getattr(current, "title", "")
        self.goal = getattr(current, "goal", "")
        self.topic = getattr(current, "notes", "") or self.goal
