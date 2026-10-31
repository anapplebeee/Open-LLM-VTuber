"""Bounded live-event director.

The director owns batching and priority. It does not call TTS or Live2D and it
does not create a second LLM loop; it invokes one async callback per selected
batch and waits for that callback before selecting the next batch.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, Literal

from loguru import logger

from .events import LiveBatch, RoomEvent

BatchHandler = Callable[[LiveBatch], Awaitable[None]]
Priority = int


class LiveDirector:
    """Single-consumer event queue with batching and simple priority rules."""

    PRIORITY: ClassVar[dict[str, Priority]] = {
        "operator": 100,
        "gift": 70,
        "chat": 40,
        "proactive": 10,
    }

    def __init__(
        self,
        *,
        window_ms: int = 3000,
        max_batch: int = 20,
        max_event_age_ms: int = 20000,
        max_queue: int = 100,
        dedupe_ttl_ms: int = 120000,
        now_ms: Callable[[], int] | None = None,
        topic_provider: Callable[[], str | None] | None = None,
    ) -> None:
        if window_ms < 0 or max_batch < 1 or max_event_age_ms < 1:
            raise ValueError("invalid director timing configuration")
        if max_queue < 1 or dedupe_ttl_ms < 1:
            raise ValueError("invalid director queue configuration")
        self.window_ms = window_ms
        self.max_batch = max_batch
        self.max_event_age_ms = max_event_age_ms
        self.max_queue = max_queue
        self.dedupe_ttl_ms = dedupe_ttl_ms
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._topic_provider = topic_provider
        self._queue: deque[RoomEvent] = deque()
        self._seen: dict[str, int] = {}
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._handler: BatchHandler | None = None
        self._last_batch: LiveBatch | None = None
        self._accepted = 0
        self._dropped = 0

    async def start(self, handler: BatchHandler) -> None:
        if self._running:
            raise RuntimeError("live director is already running")
        self._handler = handler
        self._running = True
        self._task = asyncio.create_task(self._run(), name="live-director")

    async def stop(self) -> None:
        self._running = False
        self._wakeup.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._handler = None

    def ingest(self, event: RoomEvent) -> bool:
        """Queue an event without blocking the platform callback.

        A full queue evicts the oldest wait item in the lowest-priority band
        when the new event is more important; otherwise the new event is
        dropped. Memory stays bounded either way.
        """
        if not isinstance(event, RoomEvent):
            raise TypeError("event must be a RoomEvent")
        self._prune_seen()
        event_key = self._event_key(event)
        if event_key in self._seen:
            return False
        if len(self._queue) >= self.max_queue:
            lowest = min(self.PRIORITY[item.type] for item in self._queue)
            if lowest >= self.PRIORITY[event.type]:
                self._dropped += 1
                logger.warning("Live queue full; dropping event {}", event.id)
                return False
            # Evict the oldest wait item in the lowest-priority band.
            for index, item in enumerate(self._queue):
                if self.PRIORITY[item.type] == lowest:
                    del self._queue[index]
                    self._seen.pop(self._event_key(item), None)
                    break
            self._dropped += 1
            logger.warning("Live queue full; evicted an older low-priority event")
        self._seen[event_key] = self._now_ms()
        self._accepted += 1
        self._queue.append(event)
        self._wakeup.set()
        return True

    def ingest_legacy(
        self, value: dict[str, Any], *, platform: str = "simulated"
    ) -> bool:
        return self.ingest(
            RoomEvent.from_legacy(value, platform=platform, now_ms=self._now_ms())
        )

    def status(self) -> dict[str, Any]:
        last = self._last_batch
        return {
            "running": self._running,
            "queued": len(self._queue),
            "accepted": self._accepted,
            "dropped": self._dropped,
            "last_batch": {
                "id": last.id,
                "kind": last.kind,
                "events": len(last.events),
                "priority": last.priority,
            }
            if last
            else None,
        }

    async def drain_once(self) -> LiveBatch | None:
        """Consume one currently available batch immediately (for tests/control)."""
        if not self._queue:
            return None
        batch = self._make_batch(self._drain_available())
        if batch:
            await self._dispatch(batch)
        return batch

    async def _run(self) -> None:
        while self._running:
            await self._wakeup.wait()
            self._wakeup.clear()
            if not self._queue:
                continue
            if self.window_ms:
                await asyncio.sleep(self.window_ms / 1000)
            events = self._drain_available()
            batch = self._make_batch(events)
            if batch:
                await self._dispatch(batch)
            if self._queue and self._running:
                self._wakeup.set()

    def _drain_available(self) -> list[RoomEvent]:
        now = self._now_ms()
        live: deque[RoomEvent] = deque()
        while self._queue:
            event = self._queue.popleft()
            if now - event.received_at_ms > self.max_event_age_ms:
                self._dropped += 1
                logger.info("Dropping expired live event {}", event.id)
                continue
            live.append(event)
        if not live:
            return []

        # Highest-priority band first, FIFO inside the band. A gift/operator
        # must not wait behind a long chat backlog; the consumer still
        # processes one batch at a time, so this only reorders the wait queue.
        best = max(self.PRIORITY[event.type] for event in live)
        selected: list[RoomEvent] = []
        remaining: deque[RoomEvent] = deque()
        for event in live:
            if len(selected) < self.max_batch and self.PRIORITY[event.type] == best:
                selected.append(event)
            else:
                remaining.append(event)
        self._queue = remaining
        return selected

    def _make_batch(self, events: list[RoomEvent]) -> LiveBatch | None:
        if not events:
            return None
        kind: Literal["chat", "gift", "operator", "proactive"] = events[0].type
        topic = None
        if kind == "chat" and self._topic_provider:
            topic = self._topic_provider()
        batch = LiveBatch(
            id=f"live-{uuid.uuid4().hex}",
            kind=kind,
            priority=self.PRIORITY[kind],
            events=tuple(events),
            topic=topic,
        )
        self._last_batch = batch
        return batch

    async def _dispatch(self, batch: LiveBatch) -> None:
        if not self._handler:
            logger.warning("Live director has no batch handler")
            return
        try:
            await self._handler(batch)
        except Exception:  # noqa: BLE001 - one bad handler must not kill the director
            logger.exception("Live batch {} failed", batch.id)

    @staticmethod
    def _event_key(event: RoomEvent) -> str:
        return f"{event.platform}:{event.room_id}:{event.id}"

    def _prune_seen(self) -> None:
        cutoff = self._now_ms() - self.dedupe_ttl_ms
        stale = [
            event_key for event_key, seen_at in self._seen.items() if seen_at < cutoff
        ]
        for event_key in stale:
            del self._seen[event_key]
