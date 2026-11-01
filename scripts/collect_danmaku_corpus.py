"""Collect public danmaku into an isolated test corpus.

This script never connects to OLV. It uses dyhub's multi-room API to connect
one or more public rooms, then records normalized chat/gift/member events as
JSONL. The production live-review log is not touched.

Examples:
    python scripts/collect_danmaku_corpus.py --room 708764876300 --duration 120
    python scripts/collect_danmaku_corpus.py --rooms-file data/test-danmaku/rooms.txt
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import websockets

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_API = "http://127.0.0.1:8757"
DEFAULT_WS = "ws://127.0.0.1:8757/ws"
DEFAULT_ROOMS_FILE = PROJECT_ROOT / "data" / "test-danmaku" / "rooms.txt"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "test-danmaku"
DEFAULT_TEST_ROOMS = ("708764876300",)


def normalize_room_id(value: str) -> str:
    """Accept a web_rid or a live.douyin.com URL."""
    text = str(value or "").strip()
    match = re.search(r"live\.douyin\.com/(\d{5,})", text)
    if match:
        return match.group(1)
    digits = re.sub(r"\D", "", text)
    return digits


def read_rooms(args: argparse.Namespace) -> list[str]:
    values: list[str] = []
    values.extend(args.room or [])
    if args.rooms_file:
        path = Path(args.rooms_file)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.split("#", 1)[0].strip()
                if line:
                    values.append(line)
    if not values:
        values.extend(DEFAULT_TEST_ROOMS)
    rooms: list[str] = []
    for value in values:
        room_id = normalize_room_id(value)
        if room_id and room_id not in rooms:
            rooms.append(room_id)
    return rooms


def api_json(api_base: str, method: str, path: str, body: dict[str, Any] | None = None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = Request(
        api_base.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"dyhub HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"dyhub unavailable: {exc.reason}") from exc


def connect_room(api_base: str, room_id: str) -> dict[str, Any]:
    return api_json(api_base, "POST", "/api/rooms/connect", {"roomId": room_id})


def room_info(api_base: str, room_id: str) -> dict[str, Any] | None:
    try:
        payload = api_json(api_base, "GET", f"/api/rooms/{room_id}")
        return payload.get("room")
    except RuntimeError:
        return None


def disconnect_room(api_base: str, room_id: str) -> None:
    try:
        api_json(api_base, "POST", f"/api/rooms/{room_id}/disconnect", {})
    except Exception:
        pass


async def wait_for_live_rooms(
    api_base: str, room_ids: list[str], *, timeout_s: float = 25.0
) -> dict[str, dict[str, Any]]:
    deadline = time.monotonic() + timeout_s
    live: dict[str, dict[str, Any]] = {}
    while time.monotonic() < deadline:
        for room_id in room_ids:
            info = room_info(api_base, room_id)
            if info and info.get("status") == "live":
                live[room_id] = info
            elif info and info.get("status") in {"error", "closed", "stopped"}:
                print(
                    f"[skip] {room_id}: {info.get('error') or info.get('status')}",
                    file=sys.stderr,
                )
        if len(live) == len(room_ids):
            return live
        await asyncio.sleep(2.0)
    return live


def event_record(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    user = event.get("user") if isinstance(event.get("user"), dict) else {}
    gift = None
    if event.get("type") == "gift":
        gift = {
            "name": data.get("giftName") or data.get("gift_name") or "",
            "gift_id": data.get("giftId") or "",
            "count": data.get("repeatCount") or data.get("comboCount") or data.get("count") or 1,
            "diamond_count": data.get("diamondCount"),
            "group_id": data.get("groupId") or "",
            "repeat_end": data.get("repeatEnd"),
        }
    content = data.get("content") or data.get("text") or ""
    if event.get("type") == "member":
        content = "进入直播间"
    return {
        "collected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "room_id": str(event.get("roomId") or ""),
        "event_id": str(event.get("id") or ""),
        "type": str(event.get("type") or "unknown"),
        "user_id": str(user.get("id") or ""),
        "user_name": str(user.get("nickname") or user.get("name") or ""),
        "content": str(content),
        "gift": gift,
        "ts_ms": event.get("ts") or event.get("receivedAt") or 0,
        "received_at_ms": event.get("receivedAt") or 0,
        "raw_method": event.get("rawMethod") or "",
    }


async def collect(
    *,
    api_base: str,
    ws_url: str,
    room_ids: list[str],
    duration_s: float,
    max_events: int,
    output: Path,
    keep_rooms: bool,
    event_types: list[str],
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    connected: list[str] = []
    try:
        for room_id in room_ids:
            try:
                result = connect_room(api_base, room_id)
                connected.append(room_id)
                status = (result.get("room") or {}).get("status")
                print(f"[connect] {room_id}: {status}")
            except Exception as exc:
                print(f"[connect failed] {room_id}: {exc}", file=sys.stderr)
        live = await wait_for_live_rooms(api_base, connected)
        if not live:
            raise RuntimeError("没有可用直播间；请检查房间是否正在直播")
        print("[live] " + ", ".join(f"{rid}={info.get('meta',{}).get('nickname','')}" for rid, info in live.items()))
        meta = {
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "rooms": live,
            "source": "dyhub public rooms",
        }
        meta_path = output.with_suffix(".meta.json")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        # Subscribe without roomId so dyhub sends all active rooms in one socket;
        # filter locally to the requested rooms.
        subscribe_url = ws_url
        query_parts = [f"types={quote(','.join(event_types), safe=',')}"]
        if "?" in subscribe_url:
            subscribe_url += "&" + "&".join(query_parts)
        else:
            subscribe_url += "?" + "&".join(query_parts)
        seen: set[str] = set()
        counts: Counter[str] = Counter()
        by_room: defaultdict[str, int] = defaultdict(int)
        written = 0
        deadline = time.monotonic() + max(1.0, duration_s)
        print(f"[collect] writing {output}")
        async with websockets.connect(subscribe_url, max_size=20_000_000, ping_interval=20, ping_timeout=20) as ws:
            while time.monotonic() < deadline and written < max_events:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(5.0, remaining))
                except asyncio.TimeoutError:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict) or str(event.get("type", "")).startswith("__"):
                    continue
                room_id = str(event.get("roomId") or "")
                if room_id not in live:
                    continue
                if str(event.get("type") or "") not in event_types:
                    continue
                event_id = str(event.get("id") or "")
                if event_id and event_id in seen:
                    continue
                if event_id:
                    seen.add(event_id)
                record = event_record(event)
                with output.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                written += 1
                counts[record["type"]] += 1
                by_room[room_id] += 1
                print(f"[{record['type']}] {record['user_name']}: {record['content'][:70]}")
        print(f"[done] events={written} by_type={dict(counts)} by_room={dict(by_room)}")
        print(f"[done] corpus={output}")
        return output
    finally:
        if not keep_rooms:
            for room_id in connected:
                disconnect_room(api_base, room_id)
                print(f"[disconnect] {room_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect public danmaku into a test corpus")
    parser.add_argument("--room", action="append", help="直播间 web_rid / live.douyin.com 链接，可重复")
    parser.add_argument("--rooms-file", default=str(DEFAULT_ROOMS_FILE), help="每行一个房间号/链接")
    parser.add_argument("--api-base", default=DEFAULT_API)
    parser.add_argument("--ws-url", default=DEFAULT_WS)
    parser.add_argument("--duration", type=float, default=120.0, help="采集秒数")
    parser.add_argument("--max-events", type=int, default=500)
    parser.add_argument("--types", default="chat,gift", help="事件类型，默认 chat,gift")
    parser.add_argument("--output", default="")
    parser.add_argument("--keep-rooms", action="store_true", help="采集结束后保留 dyhub 房间连接")
    args = parser.parse_args()
    rooms = read_rooms(args)
    if not rooms:
        parser.error("没有房间号")
    output = (
        Path(args.output)
        if args.output
        else DEFAULT_OUTPUT_DIR / f"corpus-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"
    )
    try:
        asyncio.run(
            collect(
                api_base=args.api_base,
                ws_url=args.ws_url,
                room_ids=rooms,
                duration_s=args.duration,
                max_events=args.max_events,
                output=output,
                keep_rooms=args.keep_rooms,
                event_types=[item.strip() for item in args.types.split(",") if item.strip()],
            )
        )
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
