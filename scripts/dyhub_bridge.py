"""Bridge dyhub's standard danmaku WebSocket into OLV's ``/live-ws``.

This is an I/O-only process: it does not run an agent, TTS, Live2D, or a second
audio path. OLV remains the single runtime. The bridge only translates dyhub
events to OLV's canonical ``RoomEvent`` and forwards them.

Typical use::

    python scripts/dyhub_bridge.py \
      --dyhub-url "ws://127.0.0.1:8757/ws?roomId=123456789&types=chat,gift" \
      --olv-url "ws://127.0.0.1:12393/live-ws"

dyhub: https://github.com/ymstar/dyhub
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import websockets
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.open_llm_vtuber.live.douyin import map_dyhub_event


def _with_room(url: str, room_id: str) -> str:
    if not room_id:
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("roomId", room_id)
    query.setdefault("types", "chat,gift,member,roomStatus")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


async def _forward(dyhub_url: str, olv_url: str) -> None:
    async with websockets.connect(
        dyhub_url,
        max_size=20_000_000,
        ping_interval=20,
        ping_timeout=20,
    ) as dyhub, websockets.connect(
        olv_url,
        max_size=20_000_000,
        ping_interval=20,
        ping_timeout=20,
    ) as olv:
        logger.info("dyhub connected: {}", dyhub_url)
        logger.info("OLV connected: {}", olv_url)

        async def drain_olv() -> None:
            async for raw in olv:
                try:
                    message = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(message, dict) and message.get("type") == "error":
                    logger.error("OLV live error: {}", message.get("message"))

        drain_task = asyncio.create_task(drain_olv())
        try:
            async for raw in dyhub:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("Ignoring non-JSON dyhub frame")
                    continue
                if not isinstance(payload, dict):
                    continue
                if payload.get("type") == "__hello":
                    logger.info("dyhub hello: {}", payload)
                    continue
                event = map_dyhub_event(payload)
                if event is None:
                    continue
                await olv.send(
                    json.dumps(
                        {
                            "type": "room-event",
                            "event": event.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    )
                )
                logger.info(
                    "forwarded {} from {}: {}",
                    event.type,
                    event.user.name or event.user.id or "unknown",
                    event.content[:80],
                )
        finally:
            drain_task.cancel()
            try:
                await drain_task
            except asyncio.CancelledError:
                pass


async def run(args: argparse.Namespace) -> None:
    dyhub_url = _with_room(args.dyhub_url, args.room_id)
    while True:
        try:
            await _forward(dyhub_url, args.olv_url)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reconnect is the intended behavior
            logger.warning("bridge disconnected: {}", exc)
        logger.info("reconnecting in {}s", args.reconnect_seconds)
        await asyncio.sleep(args.reconnect_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="dyhub -> OLV live bridge")
    parser.add_argument(
        "--dyhub-url",
        default="ws://127.0.0.1:8757/ws",
        help="dyhub WebSocket URL (may include roomId/types query)",
    )
    parser.add_argument(
        "--room-id",
        default="",
        help="optional roomId added to the dyhub URL if absent",
    )
    parser.add_argument(
        "--olv-url",
        default="ws://127.0.0.1:12393/live-ws",
        help="OLV live event WebSocket URL",
    )
    parser.add_argument("--reconnect-seconds", type=float, default=3.0)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        logger.info("bridge stopped")


if __name__ == "__main__":
    main()
