"""Write one session recap after a live session ends."""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from .store import LiveStore


class SessionRecapWriter:
    """Summarize live chat and events, with a deterministic fallback."""

    DEFAULT_SYSTEM = """你是直播场次回顾整理器。
根据主播与观众的实际对话和事件，写一段供下一场直播参考的简短回顾。
只保留事实：本场聊了什么、观众关注点、主播承诺、未完成的话题。
不要编造事实，不要写“本场直播很成功”等空话。不超过 500 字。"""

    def __init__(self, store: LiveStore, llm: Any = None) -> None:
        self.store = store
        self.llm = llm

    async def write(self, session_id: str) -> str:
        chats = list(reversed(self.store.recent_chat(session_id, limit=100)))
        events = list(reversed(self.store.recent_events(session_id, limit=100)))
        if not chats:
            summary = self._fallback(events)
        else:
            summary = await self._summarize(session_id, chats, events)
        summary = summary.strip()[:2000]
        self.store.set_recap(session_id, summary, int(time.time() * 1000))
        logger.info("Session recap written: session={} chars={}", session_id, len(summary))
        return summary

    async def _summarize(
        self, session_id: str, chats: list[dict[str, Any]], events: list[dict[str, Any]]
    ) -> str:
        transcript = "\n".join(
            f"{item['role']}: {item['content']}" for item in chats[-80:]
        )
        event_lines = "\n".join(
            f"{item['type']} {item['user_name']}: {item['content']}"
            for item in events[-40:]
        )
        if self.llm is None:
            return self._fallback(events)

        prompt = (
            f"场次：{session_id}\n\n对话记录：\n{transcript}\n\n"
            f"事件记录：\n{event_lines}\n\n请写回顾。"
        )
        try:
            chunks: list[str] = []
            async for item in self.llm.chat_completion(
                [{"role": "user", "content": prompt}], self.DEFAULT_SYSTEM
            ):
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict) and item.get("type") == "text_delta":
                    chunks.append(str(item.get("text") or ""))
            summary = "".join(chunks).strip()
            if summary:
                return summary
        except Exception:  # noqa: BLE001 - recap failure must not block session stop
            logger.exception("Session recap LLM failed; using deterministic fallback")
        return self._fallback(events)

    @staticmethod
    def _fallback(events: list[dict[str, Any]]) -> str:
        if not events:
            return "本场没有可记录的对话或事件。"
        counts: dict[str, int] = {}
        for event in events:
            counts[event["type"]] = counts.get(event["type"], 0) + 1
        summary = "本场事件统计：" + "，".join(
            f"{kind}×{count}" for kind, count in sorted(counts.items())
        )
        return summary + "。"


__all__ = ["SessionRecapWriter"]
