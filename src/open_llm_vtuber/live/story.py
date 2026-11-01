"""Long-form story mode for the live streamer.

Story mode deliberately stays outside the normal 60-character replyer. It
generates one bounded story, splits it into short chunks, and lets the live
runtime yield those chunks through the normal sentence/TTS pipeline. The
runtime can call ``interrupt_story`` between chunks when a gift or chat needs
priority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = [
    "LiveStoryTeller",
    "StoryCommand",
    "detect_story_command",
    "split_story_text",
]

_START = re.compile(
    r"^(?:(?:开始|来|给我|我想听|讲|说|故事模式|长故事).*?故事"
    r"(?:模式)?(?:\s*[：:，,]\s*|\s+)?(.*))$"
)
_START_SHORT = re.compile(
    r"^(?:故事模式|讲个故事|来个故事|长故事|开始故事|开始讲个故事)$"
)
_STOP = re.compile(
    r"^(?:停止|停|别|不要|不)(?:再)?(?:讲|说|继续)?(?:故事)?(?:了|啦)?$|^打断$"
)
_CONTINUE = re.compile(r"^(?:继续|接着|然后呢|后来呢|继续讲|接着说)(?:讲|说)?$")
_MARKDOWN = re.compile(r"```.*?```|[*_`#>]", re.DOTALL)


@dataclass(frozen=True)
class StoryCommand:
    action: str
    topic: str = ""


def detect_story_command(text: str) -> StoryCommand | None:
    clean = (text or "").strip()
    if not clean:
        return None
    if _STOP.match(clean):
        return StoryCommand("stop")
    if _CONTINUE.match(clean):
        return StoryCommand("continue")
    match = _START.match(clean)
    if match:
        before, _, after = clean.partition("故事")
        topic = re.sub(
            r"^(?:开始|来|给我|我想听|讲|说|故事模式|长故事)+",
            "",
            before,
        )
        topic = re.sub(r"^(?:一个|个|一段|段|长篇|长|的)+", "", topic)
        topic = topic.strip(" 的：:，,")
        if not topic:
            topic = after.strip(" 模式：:，,")[:80]
        return StoryCommand("start", topic[:80])
    if _START_SHORT.match(clean):
        return StoryCommand("start")
    return None


def split_story_text(text: str, *, max_chars: int = 120) -> list[str]:
    """Split a long story into TTS-friendly chunks at sentence boundaries."""
    clean = _MARKDOWN.sub("", text or "").strip()
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    if not clean:
        return []
    sentences = re.split(r"(?<=[。！？!?；;])\s*", clean)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > max_chars:
            parts = [
                sentence[index : index + max_chars]
                for index in range(0, len(sentence), max_chars)
            ]
        else:
            parts = [sentence]
        for part in parts:
            if current and len(current) + len(part) > max_chars:
                chunks.append(current)
                current = part
            else:
                current += part
    if current:
        chunks.append(current)
    return chunks


def _collect_text(stream: Any):
    async def collect() -> str:
        chunks: list[str] = []
        async for item in stream:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict) and item.get("type") == "text_delta":
                chunks.append(str(item.get("text") or ""))
        return "".join(chunks)

    return collect()


class LiveStoryTeller:
    """Generate one long, fictional, streamer-style story."""

    SYSTEM = """你是抖音直播间主播「小鲸」的故事讲述者。
请讲一个适合直播连续讲述的虚构故事，而不是文章或客服回答。
要求：
- 600～1000 个汉字，有开场钩子、人物、一个小冲突和一个悬念。
- 口语化、有画面感，句子短，方便主播一段一段念出来；不要 Markdown、不要标题、不要分点。
- 可以有少量人物对话，但不要写成剧本；不要出现“作为 AI”“以下是故事”等助手话术。
- 这是虚构故事；不要声称是真人真事，不编造现实人物隐私，不涉及政治、色情、仇恨、违法或真实医疗投资建议。
- 故事结束在自然悬念处，方便观众说“继续”。
"""

    def __init__(self, llm: Any, *, max_chars: int = 1200) -> None:
        self.llm = llm
        self.max_chars = max(300, int(max_chars))

    async def tell(
        self,
        topic: str = "",
        *,
        persona: str = "",
        previous: str = "",
    ) -> str:
        topic = (topic or "一只蓝鲸娘在深夜直播间遇到的奇怪客人").strip()
        prompt = f"故事主题：{topic}\n"
        if previous:
            prompt += f"\n上一段故事（接着讲，不要重述）：\n{previous[-1200:]}\n"
        prompt += "\n请直接开始讲故事。"
        system = self.SYSTEM
        if persona:
            system = f"{persona}\n\n{system}"
        text = await _collect_text(
            self.llm.chat_completion(
                [{"role": "user", "content": prompt}],
                system,
            )
        )
        text = _MARKDOWN.sub("", text).strip()
        # Keep one story bounded; the next 'continue' generates the next part.
        return text[: self.max_chars]
