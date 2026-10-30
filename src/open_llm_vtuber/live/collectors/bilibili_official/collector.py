"""Bilibili Open Platform collector for the OLV live director.

Credentials and the concrete socket client are deployment concerns. This class
only adapts authorized platform messages into RoomEvent objects and forwards
them to the live director.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from ...bilibili import map_bilibili_official_message
from ...events import RoomEvent
from .game_checkpoint import GameIdCheckpoint
from .websocket_client import BiliWebSocketClient

EventHandler = Callable[[RoomEvent], Awaitable[None]]


@dataclass(frozen=True)
class BilibiliOfficialConfig:
    id_code: str
    app_id: int
    access_key: str
    access_key_secret: str
    api_host: str = "https://live-open.biliapi.com"
    checkpoint_path: Path = Path("data/bili_game_id.json")

    def __post_init__(self) -> None:
        object.__setattr__(self, "app_id", int(self.app_id))
        object.__setattr__(self, "checkpoint_path", Path(self.checkpoint_path))

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("id_code", self.id_code),
                ("access_key", self.access_key),
                ("access_key_secret", self.access_key_secret),
            )
            if not value
        ]
        if missing or self.app_id <= 0:
            raise ValueError(
                "Bilibili official credentials are incomplete: "
                + ", ".join(missing or ["app_id"])
            )


class BilibiliOfficialCollector:
    """Own the official websocket task and emit normalized events."""

    def __init__(self, config: BilibiliOfficialConfig) -> None:
        config.validate()
        self.config = config
        self._client: BiliWebSocketClient | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self, on_event: EventHandler) -> None:
        if self._task and not self._task.done():
            raise RuntimeError("Bilibili collector is already running")
        checkpoint = GameIdCheckpoint(self.config.checkpoint_path)
        self._client = BiliWebSocketClient(
            id_code=self.config.id_code,
            app_id=self.config.app_id,
            access_key=self.config.access_key,
            access_key_secret=self.config.access_key_secret,
            api_host=self.config.api_host,
            game_id_checkpoint=checkpoint,
            start_error_callback=self._on_start_error,
        )

        async def handle(payload: dict[str, Any]) -> None:
            event = map_bilibili_official_message(payload)
            if event is not None:
                await on_event(event)

        self._task = asyncio.create_task(
            self._client.run(handle), name="bilibili-official-collector"
        )
        logger.info("Bilibili official collector started")

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._client = None
        logger.info("Bilibili official collector stopped")

    async def _on_start_error(self, message: str) -> None:
        logger.error("Bilibili app/start failed: {}", message)


__all__ = ["BilibiliOfficialCollector", "BilibiliOfficialConfig"]
