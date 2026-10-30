"""OLV browser speech output adapter for a live session.

The adapter uses the same TTSTaskManager and audio payload builder as the normal
OLV conversation path. It does not wait for frontend-playback-complete: a live
stream must keep moving even when the viewer tab is hidden or reconnecting.
"""

from __future__ import annotations

import asyncio
import json
import time
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
    ):
        self.context = context
        self.websocket_send = websocket_send
        self.tts_manager = TTSTaskManager()
        self.store = store
        self.session_id = session_id

    async def send(self, output: Any) -> None:
        if isinstance(output, (SentenceOutput, AudioOutput)):
            text = await process_agent_output(
                output=output,
                character_config=self.context.character_config,
                live2d_model=self.context.live2d_model,
                tts_engine=self.context.tts_engine,
                websocket_send=self.websocket_send,
                tts_manager=self.tts_manager,
                translate_engine=self.context.translate_engine,
            )
            if self.store and self.session_id and text:
                await asyncio.to_thread(
                    self.store.append_chat,
                    self.session_id,
                    "assistant",
                    text,
                    int(time.time() * 1000),
                )
        elif isinstance(output, dict) and output.get("type") == "tool_call_status":
            output["name"] = self.context.character_config.character_name
            await self.websocket_send(json.dumps(output))

    async def end_batch(self) -> None:
        had_tasks = bool(self.tts_manager.task_list)
        if had_tasks:
            await asyncio.gather(*self.tts_manager.task_list)
        await self.tts_manager.wait_idle(timeout=15)
        if had_tasks:
            await self.websocket_send(json.dumps({"type": "backend-synth-complete"}))
        await self.websocket_send(
            json.dumps({"type": "control", "text": "conversation-chain-end"})
        )
        self.tts_manager.clear()


__all__ = ["LiveBrowserOutput"]
