"""In-process bridge between the live director and an OLV agent.

The bridge deliberately accepts an async output callback instead of importing a
WebSocket handler. That keeps the live core testable and lets the OLV
conversation layer remain the single owner of TTS, playback and Live2D.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from .agent_input import make_live_batch_input
from .bilibili import map_bilibili_payload
from .director import LiveDirector
from .events import RoomEvent
from .rundown import RundownState
from .store import LiveStore

OutputHandler = Callable[[Any], Awaitable[None]]


class LiveSession:
    """One live session with a director, a rundown and one agent."""

    def __init__(
        self,
        *,
        agent: Any,
        on_output: OutputHandler,
        director: LiveDirector | None = None,
        rundown: RundownState | None = None,
        session_id: str | None = None,
        on_batch_end: Callable[[], Awaitable[None]] | None = None,
        store: LiveStore | None = None,
    ) -> None:
        self.session_id = session_id or uuid.uuid4().hex
        self.agent = agent
        self.on_output = on_output
        self.on_batch_end = on_batch_end
        self.rundown = rundown
        self.store = store
        self.director = director or LiveDirector(
            topic_provider=lambda: (
                self.rundown.current().goal
                if self.rundown and self.rundown.current()
                else None
            )
        )
        self._running = False

    async def start(self) -> None:
        if self._running:
            raise RuntimeError("live session is already running")
        if self.rundown:
            self.rundown.begin("system")
        if self.store:
            await asyncio.to_thread(
                self.store.start_session, self.session_id, int(time.time() * 1000)
            )
        await self.director.start(self._handle_batch)
        self._running = True

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        await self.director.stop()
        if self.rundown:
            self.rundown.finish("stop")
        if self.store:
            await asyncio.to_thread(
                self.store.end_session, self.session_id, int(time.time() * 1000)
            )

    def ingest(self, event: RoomEvent) -> bool:
        if not self._running:
            return False
        return self.director.ingest(event)

    def ingest_bilibili(self, payload: dict[str, Any]) -> bool:
        if not self._running:
            return False
        event = map_bilibili_payload(payload)
        if event is None:
            return False
        return self.director.ingest(event)

    def status(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "running": self._running,
            "director": self.director.status(),
            "rundown": self.rundown.snapshot() if self.rundown else None,
        }

    async def _handle_batch(self, batch: Any) -> None:
        if self.store:
            await asyncio.to_thread(
                self.store.append_events, self.session_id, batch.events
            )
        async for output in self.agent.chat(make_live_batch_input(batch)):
            await self.on_output(output)
        if self.on_batch_end:
            await self.on_batch_end()
        logger.debug("Live batch {} handed to OLV output handler", batch.id)
