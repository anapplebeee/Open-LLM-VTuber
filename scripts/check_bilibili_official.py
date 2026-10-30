"""Single-room Bilibili Open Platform collector smoke test.

Usage (PowerShell):
    $env:BILI_ID_CODE = "..."
    $env:BILI_APP_ID = "123"
    $env:BILI_ACCESS_KEY = "..."
    $env:BILI_ACCESS_KEY_SECRET = "..."
    uv run python scripts/check_bilibili_official.py --seconds 60

The secret is read from the environment and is never printed. This script only
validates collection; it does not start OLV or TTS.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.open_llm_vtuber.live.collectors.bilibili_official import (
    BilibiliOfficialCollector,
    BilibiliOfficialConfig,
)
from src.open_llm_vtuber.live.events import RoomEvent


async def run(seconds: int) -> None:
    config = BilibiliOfficialConfig.from_env(os.environ)
    config.validate()
    logger.info(
        "Bilibili collector config: app_id={} id_code={} access_key={} secret={}",
        config.app_id,
        "set" if config.id_code else "missing",
        "set" if config.access_key else "missing",
        "set" if config.access_key_secret else "missing",
    )

    collector = BilibiliOfficialCollector(config)
    seen = 0

    async def on_event(event: RoomEvent) -> None:
        nonlocal seen
        seen += 1
        detail = f"gift={event.gift.name}×{event.gift.count}" if event.gift else event.content
        logger.info(
            "event #{} type={} user={} detail={}",
            seen,
            event.type,
            event.user.name,
            detail,
        )

    await collector.start(on_event)
    logger.info("Listening for {} seconds; open the room and send a test danmaku...", seconds)
    try:
        if seconds > 0:
            await asyncio.sleep(seconds)
        else:
            await asyncio.Event().wait()
    finally:
        await collector.stop()
        logger.info("Stopped; events observed={}", seen)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seconds",
        type=int,
        default=60,
        help="run duration; 0 means until Ctrl+C",
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(args.seconds))
    except KeyboardInterrupt:
        logger.info("Interrupted")


if __name__ == "__main__":
    main()
