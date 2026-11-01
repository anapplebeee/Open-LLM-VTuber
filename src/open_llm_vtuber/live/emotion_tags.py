"""Chinese labels for the live emotion tags.

The Live2D expression names (happy, anger, ...) stay internal.  The language
model and the operator-facing prompt use Chinese labels; both Chinese and
English tags are accepted at the boundary.
"""

from __future__ import annotations

import re

__all__ = [
    "EMOTION_LABELS",
    "EN_TO_ZH",
    "ZH_TO_EN",
    "chinese_label",
    "normalize_emotion_tags",
]

# Canonical English key -> Chinese label shown in prompts.
EMOTION_LABELS: dict[str, str] = {
    "neutral": "平静",
    "joy": "开心",
    "smirk": "得意",
    "fear": "害怕",
    "surprise": "惊讶",
    "sadness": "难过",
    "anger": "生气",
    "disgust": "嫌弃",
}
EN_TO_ZH = dict(EMOTION_LABELS)
ZH_TO_EN = {label: key for key, label in EMOTION_LABELS.items()}

# Accept a few common synonyms in model output without advertising them.
_ZH_SYNONYMS = {
    "中性": "neutral",
    "高兴": "joy",
    "快乐": "joy",
    "害羞": "smirk",
    "紧张": "fear",
    "震惊": "surprise",
    "伤心": "sadness",
    "愤怒": "anger",
    "厌恶": "disgust",
}
_ZH_TO_EN_ALL = {**ZH_TO_EN, **_ZH_SYNONYMS}
_ZH_TAG = re.compile(r"\[(" + "|".join(_ZH_TO_EN_ALL) + r")\]")


def chinese_label(value: str) -> str:
    return EMOTION_LABELS.get((value or "").lower(), value)


def normalize_emotion_tags(text: str) -> str:
    """Convert Chinese tags to the canonical English tags used internally."""

    def replace(match: re.Match[str]) -> str:
        return f"[{_ZH_TO_EN_ALL[match.group(1)]}]"

    return _ZH_TAG.sub(replace, text or "")
