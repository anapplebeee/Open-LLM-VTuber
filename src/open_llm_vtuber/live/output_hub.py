"""Fan-out of OLV live outputs to browser viewers.

The collector that submits a ``douyin-event`` is not necessarily the browser
that should play the resulting audio/Live2D actions.  This hub keeps those
concerns separate: the live producer owns the session, while any number of
read-only viewer sockets receive the exact same OLV output messages.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

__all__ = ["LiveOutputHub"]

SendText = Callable[[str], Awaitable[None]]


class LiveOutputHub:
    def __init__(self) -> None:
        self._clients: dict[Any, SendText] = {}
        self._lock = asyncio.Lock()

    async def register(self, client: Any) -> None:
        send_text = getattr(client, "send_text", None)
        if not callable(send_text):
            raise TypeError("live view client must expose send_text")
        async with self._lock:
            self._clients[client] = send_text

    async def unregister(self, client: Any) -> None:
        async with self._lock:
            self._clients.pop(client, None)

    @property
    def count(self) -> int:
        return len(self._clients)

    async def broadcast(self, payload: str) -> int:
        """Send one payload to every viewer; drop clients that fail."""
        async with self._lock:
            snapshot = list(self._clients.items())
        if not snapshot:
            return 0

        results = await asyncio.gather(
            *(self._send_one(client, send, payload) for client, send in snapshot),
            return_exceptions=True,
        )
        delivered = 0
        dead: list[Any] = []
        for (client, _), result in zip(snapshot, results):
            if result is True:
                delivered += 1
            else:
                dead.append(client)
        if dead:
            async with self._lock:
                for client in dead:
                    self._clients.pop(client, None)
        return delivered

    @staticmethod
    async def _send_one(client: Any, send: SendText, payload: str) -> bool:
        try:
            await send(payload)
            return True
        except Exception as exc:  # noqa: BLE001 - a dead viewer must not stop live audio
            logger.debug("Live view send failed ({}): {}", type(client).__name__, exc)
            return False
