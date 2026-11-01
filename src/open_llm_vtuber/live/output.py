"""OLV browser speech output adapter for a live session.

The adapter uses the same TTSTaskManager and audio payload builder as the normal
OLV conversation path. It does not wait for frontend-playback-complete: a live
stream must keep moving even when the viewer tab is hidden or reconnecting.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from ..agent.output_types import AudioOutput, SentenceOutput
from ..conversations.conversation_utils import process_agent_output
from ..conversations.tts_manager import TTSTaskManager
from ..conversations.types import WebSocketSend
from ..service_context import ServiceContext
from .store import LiveStore


class LiveBrowserOutput:
    """Render live agent outputs through OLV's existing browser audio path."""

    def __init__(
        self,
        context: ServiceContext,
        websocket_send: WebSocketSend,
        *,
        store: LiveStore | None = None,
        session_id: str | None = None,
        broadcast_send: Callable[[str], Awaitable[int]] | None = None,
    ):
        self.context = context
        self.websocket_send = websocket_send
        self.broadcast_send = broadcast_send
        self.tts_manager = TTSTaskManager()
        self.store = store
        self.session_id = session_id

    async def _send_text(self, payload: str) -> None:
        """Send to the collector and to independent browser viewers."""
        if self.broadcast_send is not None:
            await self.broadcast_send(payload)
        await self.websocket_send(payload)

    async def send_event(self, payload: dict[str, Any]) -> None:
        """Send a non-agent live event to the collector and viewers."""
        await self._send_text(json.dumps(payload, ensure_ascii=False))

    async def send(self, output: Any) -> None:
        if isinstance(output, (SentenceOutput, AudioOutput)):
            await process_agent_output(
                output=output,
                character_config=self.context.character_config,
                live2d_model=self.context.live2d_model,
                tts_engine=self.context.tts_engine,
                websocket_send=self._send_text,
                tts_manager=self.tts_manager,
                translate_engine=self.context.translate_engine,
            )
            # Dialogue persistence is owned by LiveSession._handle_batch,
            # which writes one aggregate assistant row per batch. Writing here
            # too would duplicate every sentence in live_chat.
        elif isinstance(output, dict) and output.get("type") == "tool_call_status":
            output["name"] = self.context.character_config.character_name
            await self._send_text(json.dumps(output))

    def close(self) -> None:
        """Cancel in-flight TTS tasks and the ordered sender on session close."""
        for task in self.tts_manager.task_list:
            if not task.done():
                task.cancel()
        self.tts_manager.clear()

    async def interrupt(self) -> None:
        """Stop queued story speech while preserving the audio owner."""
        tasks = list(self.tts_manager.task_list)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tts_manager.clear()
        await self._send_text(
            json.dumps({"type": "control", "text": "conversation-chain-end"})
        )

    async def end_batch(self) -> None:
        had_tasks = bool(self.tts_manager.task_list)
        if had_tasks:
            await asyncio.gather(*self.tts_manager.task_list, return_exceptions=True)
        await self.tts_manager.wait_idle(timeout=15)
        if had_tasks:
            await self._send_text(json.dumps({"type": "backend-synth-complete"}))
        await self._send_text(
            json.dumps({"type": "control", "text": "conversation-chain-end"})
        )
        self.tts_manager.clear()


__all__ = ["LiveBrowserOutput"]
