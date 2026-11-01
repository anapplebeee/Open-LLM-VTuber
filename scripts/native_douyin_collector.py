"""Forward native Douyin events into OLV's ``/live-ws``.

This replaces ``scripts/dyhub_bridge.py``: it does not start or connect to a
dyhub HTTP/WS data plane.  The native Python collector talks to Douyin
directly; Node.js is invoked only to compute the WebSocket signature via
``scripts/douyin_sign.mjs`` (it is not a data plane).

Typical use::

    python scripts/native_douyin_collector.py \
      --room-id 123456789 \
      --olv-url ws://127.0.0.1:12393/live-ws \
      --cookie "ttwid=...; sessionid=..."

The OLV live session must already be running: ``/live-ws`` rejects producers
until the browser has an active live session.  The forwarder reconnects with
exponential backoff and keeps one unsent event across reconnects.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import websockets
from loguru import logger

from src.open_llm_vtuber.live.collectors.douyin_native import (
    NativeDouyinCollector,
    NodeUnavailableError,
    RoomClosedError,
)

_OLV_MAX_SIZE = 20_000_000
_OLV_PING_INTERVAL = 20.0
_OLV_RECONNECT_BASE = 2.0
_OLV_RECONNECT_MAX = 30.0


def _brief(error: BaseException) -> str:
    text = " ".join(str(error).split())
    return f"{type(error).__name__}: {text[:300]}" if text else type(error).__name__


async def _forward_events(
    collector: NativeDouyinCollector,
    olv_url: str,
    stop_event: asyncio.Event,
) -> None:
    """Maintain the OLV socket and forward one canonical event at a time."""

    pending = None
    delay = _OLV_RECONNECT_BASE
    while not stop_event.is_set():
        try:
            async with websockets.connect(
                olv_url,
                max_size=_OLV_MAX_SIZE,
                ping_interval=_OLV_PING_INTERVAL,
                ping_timeout=_OLV_PING_INTERVAL,
            ) as olv:
                logger.info("OLV live-ws connected: {}", olv_url)
                delay = _OLV_RECONNECT_BASE
                while not stop_event.is_set():
                    if pending is None:
                        pending = await collector.next_event()
                    await olv.send(
                        json.dumps(
                            {
                                "type": "room-event",
                                "event": pending.model_dump(mode="json"),
                            },
                            ensure_ascii=False,
                        )
                    )
                    logger.info(
                        "forwarded {} from {}: {}",
                        pending.type,
                        pending.user.name or pending.user.id or "unknown",
                        pending.content[:80],
                    )
                    pending = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reconnect is intended
            if stop_event.is_set():
                break
            logger.warning(
                "OLV forwarder disconnected: {}; retrying in {:.1f}s",
                _brief(exc),
                delay,
            )
        if stop_event.is_set():
            break
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
        delay = min(delay * 2, _OLV_RECONNECT_MAX)


async def _run(args: argparse.Namespace) -> int:
    cookie = args.cookie or os.environ.get("DOUYIN_COOKIE", "")
    collector = NativeDouyinCollector(
        args.room_id,
        cookie=cookie,
        node_path=args.node,
    )
    stop_event = asyncio.Event()
    forward_task = asyncio.create_task(
        _forward_events(collector, args.olv_url, stop_event),
        name="douyin-olv-forwarder",
    )
    exit_code = 0
    try:
        logger.info("starting native Douyin collector for room {}", args.room_id)
        await collector.run()
    except RoomClosedError as exc:
        logger.info("Douyin room is not collectable: {}", exc)
    except NodeUnavailableError as exc:
        logger.error("Douyin signature helper unavailable: {}", exc)
        exit_code = 1
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - report and exit non-zero
        logger.error("native Douyin collector failed: {}", _brief(exc))
        exit_code = 1
    finally:
        stop_event.set()
        forward_task.cancel()
        try:
            await forward_task
        except asyncio.CancelledError:
            logger.debug("OLV forwarder cancelled")
        except Exception as exc:  # noqa: BLE001 - shutdown must not raise
            logger.debug("OLV forwarder shutdown error: {}", _brief(exc))
        await collector.stop()
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Native Douyin collector -> OLV live-ws bridge "
            "(no dyhub server, no dyhub_bridge.py)"
        )
    )
    parser.add_argument(
        "--room-id",
        required=True,
        help="Douyin web_rid (e.g. 123456789) or live.douyin.com URL",
    )
    parser.add_argument(
        "--olv-url",
        default="ws://127.0.0.1:12393/live-ws",
        help="OLV live event WebSocket URL",
    )
    parser.add_argument(
        "--cookie",
        default="",
        help=(
            "optional Douyin cookie header (or set DOUYIN_COOKIE); "
            "the value is never logged"
        ),
    )
    parser.add_argument(
        "--node",
        default=None,
        help="optional Node.js executable for signing (default: NODE/PATH)",
    )
    args = parser.parse_args()
    if not args.olv_url.startswith(("ws://", "wss://")):
        parser.error("--olv-url must start with ws:// or wss://")
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        logger.info("native Douyin collector stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
