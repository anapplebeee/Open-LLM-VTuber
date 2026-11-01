"""Replay a collected danmaku corpus as a manual/offline test sheet.

Default mode is safe: it only writes a Markdown test sheet and prints events.
It never sends events to the production OLV instance.

To test against a second, non-live OLV instance explicitly:
    python scripts/replay_danmaku_corpus.py corpus.jsonl \
      --target-ws ws://127.0.0.1:12394/live-ws --allow-live
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import websockets

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "test-danmaku"


def load_corpus(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("content"):
                events.append(item)
    events.sort(key=lambda item: int(item.get("ts_ms") or 0))
    return events


def to_room_event(item: dict[str, Any], index: int) -> dict[str, Any]:
    event_type = str(item.get("type") or "chat")
    if event_type == "member":
        event_type = "join"
    if event_type not in {"chat", "gift", "join", "operator"}:
        event_type = "chat"
    raw_gift = item.get("gift") if isinstance(item.get("gift"), dict) else None
    gift = None
    if event_type == "gift" and raw_gift:
        gift = {
            "name": raw_gift.get("name") or "礼物",
            "count": int(raw_gift.get("count") or 1),
        }
    return {
        "id": f"corpus-{item.get('event_id') or index}",
        "type": event_type,
        "platform": "douyin",
        "room_id": item.get("room_id") or "",
        "user": {
            "id": item.get("user_id") or "",
            "name": item.get("user_name") or "测试观众",
        },
        "content": item.get("content") or "",
        "gift": gift,
        "received_at_ms": int(datetime.now().timestamp() * 1000),
    }


def write_sheet(events: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 弹幕回放测试表",
        "",
        f"来源：`{len(events)}` 条采集事件",
        "",
        "| # | 房间 | 用户 | 弹幕 | 期望回复 | 实际回复 | 结果 |",
        "|---:|---|---|---|---|---|---|",
    ]
    for index, item in enumerate(events, 1):
        content = str(item.get("content") or "").replace("|", "\\|").replace("\n", " ")
        user = str(item.get("user_name") or "").replace("|", "\\|")
        room = str(item.get("room_id") or "").replace("|", "\\|")
        lines.append(f"| {index} | {room} | {user} | {content} |  |  |  |")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def send_to_ws(events: list[dict[str, Any]], target_ws: str, delay_s: float) -> None:
    async with websockets.connect(target_ws, max_size=20_000_000, ping_interval=20, ping_timeout=20) as ws:
        print(f"[replay] connected {target_ws}")
        for index, item in enumerate(events, 1):
            event = to_room_event(item, index)
            await ws.send(json.dumps({"type": "room-event", "event": event}, ensure_ascii=False))
            print(f"[send {index}/{len(events)}] {event['user']['name']}: {event['content'][:70]}")
            await asyncio.sleep(max(0.0, delay_s))


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay danmaku corpus safely")
    parser.add_argument("corpus", help="collect_danmaku_corpus.py 生成的 JSONL")
    parser.add_argument("--output", default="", help="Markdown 测试表路径")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--target-ws", default="", help="第二套 OLV 的 live-ws 地址")
    parser.add_argument("--allow-live", action="store_true", help="显式允许发送到 OLV（默认不发）")
    parser.add_argument("--delay", type=float, default=4.0, help="发送间隔秒数")
    args = parser.parse_args()
    corpus_path = Path(args.corpus)
    events = load_corpus(corpus_path)
    if args.limit > 0:
        events = events[: args.limit]
    if not events:
        parser.error("语料为空")
    output = Path(args.output) if args.output else corpus_path.with_suffix(".test-sheet.md")
    write_sheet(events, output)
    print(f"[sheet] {output} ({len(events)} events)")
    if args.target_ws:
        if not args.allow_live:
            parser.error("--target-ws 需要同时显式加 --allow-live；默认不会连接任何 OLV")
        asyncio.run(send_to_ws(events, args.target_ws, args.delay))


if __name__ == "__main__":
    main()
