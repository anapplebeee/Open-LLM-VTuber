"""Strip internal identifiers before text reaches the model or TTS.

Adapted from Amaidesu's streamer term sanitizer (MIT), narrowed to the
identifiers that can leak into OLV's spoken output. It never changes the
meaningful part of a line; it only replaces opaque task/observation handles
with human-readable descriptions.
"""

from __future__ import annotations

import re

__all__ = ["sanitize_internal_terms"]

_TASK_ID_PATTERN = re.compile(r"deleg_[0-9]+(?:_[0-9]+)*")
_OBS_REF_PATTERN = re.compile(r"obs_[0-9a-f]{6,}(?:_[0-9a-f]{6,})*")
_SIMULATED_PREFIX_PATTERN = re.compile(r"\bsimulated:")


def sanitize_internal_terms(text: str) -> str:
    if not text:
        return text
    text = _TASK_ID_PATTERN.sub("一个任务", text)
    text = _OBS_REF_PATTERN.sub("一次观察", text)
    return _SIMULATED_PREFIX_PATTERN.sub("", text)
