"""Character-bigram repeat gate for live speech.

Adapted from Amaidesu's streamer speech gate (MIT). It is deliberately a
pure in-memory check: no tokenizer, no model call and no second agent loop.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["SpeechGate", "SpeechGateVerdict", "speech_overlap_ratio"]

NGRAM_SIZE = 2
SPEECH_DUPLICATE_RATIO = 0.5
SPEECH_GATE_WINDOW = 10

_NUMBER = re.compile(r"[0-9０-９]+(?:[.．][0-9０-９]+)*|[零一二两三四五六七八九十百千万亿]+")
_ASCII_TOKEN = re.compile(r"[A-Za-z_]+")


def _bigrams(text: str) -> set[str]:
    chars = [char for char in (text or "") if not char.isspace()]
    return {
        "".join(chars[index : index + NGRAM_SIZE])
        for index in range(len(chars) - NGRAM_SIZE + 1)
    }


def _normalized(text: str) -> str:
    return _ASCII_TOKEN.sub(" ", _NUMBER.sub("N", text or ""))


def speech_overlap_ratio(speech: str, recent_speeches: Sequence[str]) -> float:
    # Short interjections ("好呀。", "4。") are not long enough for a
    # meaningful bigram comparison and must not be suppressed as repeats.
    if len((speech or "").strip()) < 12:
        return 0.0
    candidate = _bigrams(_normalized(speech))
    if len(candidate) < 6:
        return 0.0
    known: set[str] = set()
    for previous in recent_speeches:
        known |= _bigrams(_normalized(previous))
    return len(candidate & known) / len(candidate) if known else 0.0


@dataclass(frozen=True)
class SpeechGateVerdict:
    passed: bool
    ratio: float


class SpeechGate:
    def __init__(
        self, *, window: int = SPEECH_GATE_WINDOW, ratio: float = SPEECH_DUPLICATE_RATIO
    ) -> None:
        self.window = max(0, int(window))
        self.ratio = float(ratio)

    def verdict(self, speech: str, recent_speeches: Sequence[str]) -> SpeechGateVerdict:
        corpus = list(recent_speeches)[-self.window :] if self.window else []
        ratio = speech_overlap_ratio(speech, corpus)
        return SpeechGateVerdict(ratio < self.ratio, ratio)
